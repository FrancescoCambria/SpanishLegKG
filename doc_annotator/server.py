#!/usr/bin/env python3
import os
import json
import csv
import io
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
import sys
import subprocess
import re
import hashlib

BASE_DIR = Path(__file__).resolve().parent
WORKSPACE_DIR = BASE_DIR.parent
DEFAULT_CSV_PATH = BASE_DIR / "annotations.csv"
SETTINGS_PATH = BASE_DIR / "settings.json"
CACHE_DIR = BASE_DIR / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_SETTINGS = {
    "target_property": "urlPdf",
    "csv_path": str(DEFAULT_CSV_PATH),
    "active_source": "Catalonia/data/cido_subset_100.json",
    "key_presets": ["law_id", "article", "entity", "jurisdiction", "date", "status", "penalty", "category"],
    "categories": [
        {"name": "Legal Reference", "color": "#3b82f6"},
        {"name": "Entity / Institution", "color": "#10b981"},
        {"name": "Obligation / Rule", "color": "#f59e0b"},
        {"name": "Date / Procedure", "color": "#8b5cf6"},
        {"name": "Penalty / Sanction", "color": "#ef4444"},
        {"name": "General Note", "color": "#6b7280"}
    ]
}

def parse_document_sections(text):
    """
    Parses full document text into structured sections (Articles, Body, Signatures, Annexes, Dispositions, Preamble).
    Default fallback for non-sectioned text is 'Body'.
    """
    if not text or not text.strip():
        return [{"type": "body", "title": "Body", "start_offset": 0, "end_offset": 0}]

    CHAPTER_PAT = re.compile(r'(?:^|\n)\s*((?:Capítol|Capítulo|Títol|Título|Secció|Sección)\s+(?:preliminar|[I|V|X|L|C]+|\d+[\w\-]*)[^\n]*)', re.IGNORECASE)
    ARTICLE_PAT = re.compile(r'(?:^|\n)\s*((?:Article|Artículo|Art\.)\s+(?:únic|único|primer|segon|tercer|quart|cinquè|sisè|setè|vuitè|novè|desè|\d+[\w\-]*)[^\n]*)', re.IGNORECASE)
    DISPOSITION_PAT = re.compile(r'(?:^|\n)\s*((?:Disposició|Disposición)\s+(?:adicional|addicional|transitòria|transitoria|derogatòria|derogatoria|final)[^\n]*)', re.IGNORECASE)
    ANNEX_PAT = re.compile(r'(?:^|\n)\s*((?:Annex|Anexo)\s*(?:\d*|\b[I|V|X|L|C]+\b)?[^\n]*)', re.IGNORECASE)
    SIGNATURE_PAT = re.compile(r'(?:^|\n)\s*((?:Barcelona|Palau de la Generalitat|Palacio de la Generalidad|Madrid|Girona|Lleida|Tarragona|Firma|Signatura|En\s+cumplimiento|Por\s+tanto)\b[^\n]*,\s*.*?\d{4})', re.IGNORECASE)
    PREAMBLE_PAT = re.compile(r'(?:^|\n)\s*((?:Preàmbul|Preámbulo|Exposició\s+de\s+motius|Exposición\s+de\s+motivos)\b[^\n]*)', re.IGNORECASE)

    matches = []
    
    for m in PREAMBLE_PAT.finditer(text):
        matches.append((m.start(1), "preamble", m.group(1).strip()))
    for m in CHAPTER_PAT.finditer(text):
        matches.append((m.start(1), "chapter", m.group(1).strip()))
    for m in ARTICLE_PAT.finditer(text):
        matches.append((m.start(1), "article", m.group(1).strip()))
    for m in DISPOSITION_PAT.finditer(text):
        matches.append((m.start(1), "disposition", m.group(1).strip()))
    for m in ANNEX_PAT.finditer(text):
        matches.append((m.start(1), "annex", m.group(1).strip()))
    for m in SIGNATURE_PAT.finditer(text):
        matches.append((m.start(1), "signature", "Signatures: " + m.group(1).strip()[:40]))

    matches.sort(key=lambda x: x[0])

    sections = []
    if not matches:
        sections.append({
            "type": "body",
            "title": "Body",
            "start_offset": 0,
            "end_offset": len(text)
        })
        return sections

    # Lead-up text before first matched section header defaults to 'Body' (not Preamble!)
    if matches[0][0] > 0:
        sections.append({
            "type": "body",
            "title": "Body",
            "start_offset": 0,
            "end_offset": matches[0][0]
        })

    for i, (start, sec_type, title) in enumerate(matches):
        end = matches[i+1][0] if i + 1 < len(matches) else len(text)
        clean_title = title if len(title) <= 80 else title[:80] + "..."
        sections.append({
            "type": sec_type,
            "title": clean_title,
            "start_offset": start,
            "end_offset": end
        })

    return sections

def load_settings():
    if SETTINGS_PATH.exists():
        try:
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                settings = json.load(f)
                for k, v in DEFAULT_SETTINGS.items():
                    if k not in settings:
                        settings[k] = v
                return settings
        except Exception as e:
            print("Error loading settings:", e)
    return dict(DEFAULT_SETTINGS)

def save_settings(settings):
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)

def get_csv_annotations(csv_path_str):
    csv_path = Path(csv_path_str)
    if not csv_path.is_absolute():
        csv_path = WORKSPACE_DIR / csv_path
    
    annotations = []
    if not csv_path.exists():
        return annotations

    try:
        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    row["start_offset"] = int(row["start_offset"])
                    row["end_offset"] = int(row["end_offset"])
                except (ValueError, TypeError):
                    pass
                if not row.get("section_title"):
                    row["section_title"] = "Body"
                if not row.get("section_type"):
                    row["section_type"] = "body"
                annotations.append(row)
    except Exception as e:
        print(f"Error reading CSV {csv_path}: {e}")
    
    return annotations

def save_csv_annotations(csv_path_str, annotations):
    csv_path = Path(csv_path_str)
    if not csv_path.is_absolute():
        csv_path = WORKSPACE_DIR / csv_path

    csv_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "doc_id", "doc_title", "doc_source", "annotation_id",
        "target_property", "selected_text", "start_offset", "end_offset",
        "section_title", "section_type", "category", "comment",
        "key_values_json", "created_at", "doc_metadata_json"
    ]

    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for ann in annotations:
            if not ann.get("section_title"):
                ann["section_title"] = "Body"
            if not ann.get("section_type"):
                ann["section_type"] = "body"
            row = {fn: ann.get(fn, "") for fn in fieldnames}
            writer.writerow(row)

def scan_sources():
    sources = []
    known_paths = [
        "Catalonia/data/cido_subset_100.json",
        "Catalonia/data/bopl_documents.json",
        "Catalonia/data/dogc_documents.json",
        "Catalonia/data/bopg_bopt_documents.json",
        "Catalonia/data/cido_structured_output",
        "Catalonia/data/structured_output",
        "National/data/boe_documents.json"
    ]

    for rel in known_paths:
        full = WORKSPACE_DIR / rel
        if full.exists():
            sources.append({
                "path": rel,
                "type": "directory" if full.is_dir() else "file",
                "name": full.name,
                "size_mb": round(full.stat().st_size / (1024 * 1024), 2) if full.is_file() else None
            })

    for root, dirs, files in os.walk(WORKSPACE_DIR):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d != "node_modules" and d != "doc_annotator"]
        rel_root = os.path.relpath(root, WORKSPACE_DIR)
        for f in files:
            if f.endswith(".json") and not f.startswith("."):
                rel_path = os.path.normpath(os.path.join(rel_root, f))
                if not any(s["path"] == rel_path for s in sources):
                    sources.append({
                        "path": rel_path,
                        "type": "file",
                        "name": f,
                        "size_mb": round((Path(root)/f).stat().st_size / (1024 * 1024), 2)
                    })
                if len(sources) >= 50:
                    break

    return sources

def load_documents_from_source(source_path_str):
    full_path = WORKSPACE_DIR / source_path_str
    if not full_path.exists():
        full_path = Path(source_path_str)

    if not full_path.exists():
        return {"error": f"Source path not found: {source_path_str}"}

    docs = []

    if full_path.is_file():
        try:
            with open(full_path, "r", encoding="utf-8") as f:
                content = json.load(f)
                if isinstance(content, list):
                    for idx, item in enumerate(content):
                        doc_id = str(item.get("cidoId") or item.get("identificador") or item.get("documentId") or item.get("id") or f"doc_{idx}")
                        title = str(item.get("recordTitle") or item.get("title") or item.get("nombre") or f"Document #{idx+1}")
                        docs.append({
                            "id": doc_id,
                            "index": idx,
                            "title": title,
                            "data": item
                        })
                elif isinstance(content, dict):
                    if "cidoId" in content or "title" in content or "identificador" in content:
                        doc_id = str(content.get("cidoId") or content.get("identificador") or "doc_0")
                        title = str(content.get("recordTitle") or content.get("title") or "Document 1")
                        docs.append({"id": doc_id, "index": 0, "title": title, "data": content})
                    else:
                        for idx, (k, v) in enumerate(content.items()):
                            if isinstance(v, dict):
                                doc_id = str(v.get("cidoId") or v.get("identificador") or k)
                                title = str(v.get("recordTitle") or v.get("title") or k)
                                docs.append({"id": doc_id, "index": idx, "title": title, "data": v})
        except Exception as e:
            return {"error": f"Failed to parse JSON file: {str(e)}"}

    elif full_path.is_dir():
        json_files = sorted(list(full_path.glob("*.json")))
        for idx, jf in enumerate(json_files):
            try:
                with open(jf, "r", encoding="utf-8") as f:
                    item = json.load(f)
                    doc_id = str(item.get("cidoId") or item.get("identificador") or jf.stem)
                    title = str(item.get("recordTitle") or item.get("title") or jf.name)
                    docs.append({
                        "id": doc_id,
                        "index": idx,
                        "title": title,
                        "data": item,
                        "file_name": jf.name
                    })
            except Exception as e:
                print(f"Error reading {jf}: {e}")

    return {"source": source_path_str, "total": len(docs), "documents": docs}

def fetch_url_text(doc_id, url_pdf, url_html, req_type):
    """
    Fetches and extracts text from urlPdf or urlHtml with disk caching,
    and returns parsed structural sections (Articles, Body, Signatures, Annexes).
    """
    cache_key = f"{doc_id}_{req_type}"
    cache_file = CACHE_DIR / f"{cache_key}.txt"

    extracted_text = ""
    cached = False

    if cache_file.exists():
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                extracted_text = f.read()
                cached = True
        except Exception:
            pass

    if not extracted_text:
        if req_type == "pdf" and url_pdf:
            try:
                res = subprocess.run(["curl", "-k", "-s", "-L", url_pdf], stdout=subprocess.PIPE, timeout=20)
                if len(res.stdout) > 300 and res.stdout.startswith(b"%PDF"):
                    p = subprocess.Popen(["pdftotext", "-", "-"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    out, _ = p.communicate(input=res.stdout, timeout=15)
                    extracted_text = out.decode("utf-8", errors="ignore").strip()
            except Exception as e:
                print(f"PDF extraction error for {url_pdf}: {e}")

        elif req_type == "html" and url_html:
            try:
                res = subprocess.run(["curl", "-k", "-s", "-L", url_html], stdout=subprocess.PIPE, timeout=20)
                html_raw = res.stdout.decode("utf-8", errors="ignore")
                if len(html_raw) > 100:
                    clean = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html_raw, flags=re.DOTALL | re.IGNORECASE)
                    clean = re.sub(r"<[^>]+>", " ", clean)
                    clean = re.sub(r"\s+", " ", clean).strip()
                    extracted_text = clean
            except Exception as e:
                print(f"HTML extraction error for {url_html}: {e}")

        if extracted_text:
            try:
                with open(cache_file, "w", encoding="utf-8") as f:
                    f.write(extracted_text)
            except Exception as e:
                print("Error writing to cache:", e)

    if extracted_text:
        sections = parse_document_sections(extracted_text)
        return {"status": "success", "text": extracted_text, "sections": sections, "cached": cached, "type": req_type}

    return {"status": "error", "message": f"Could not extract text from {req_type} URL", "type": req_type}

class AnnotatorHTTPRequestHandler(BaseHTTPRequestHandler):
    def _send_response_headers(self, code=200, content_type="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_OPTIONS(self):
        self._send_response_headers(200, "text/plain")

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        if path == "/api/sources":
            sources = scan_sources()
            self._send_response_headers(200)
            self.wfile.write(json.dumps(sources).encode("utf-8"))

        elif path == "/api/documents":
            source_path = query.get("source", ["Catalonia/data/cido_subset_100.json"])[0]
            res = load_documents_from_source(source_path)
            self._send_response_headers(200)
            self.wfile.write(json.dumps(res).encode("utf-8"))

        elif path == "/api/fetch_text":
            doc_id = query.get("doc_id", [""])[0]
            url_pdf = query.get("url_pdf", [""])[0]
            url_html = query.get("url_html", [""])[0]
            req_type = query.get("type", ["pdf"])[0]

            res = fetch_url_text(doc_id, url_pdf, url_html, req_type)
            self._send_response_headers(200)
            self.wfile.write(json.dumps(res).encode("utf-8"))

        elif path == "/api/annotations":
            settings = load_settings()
            csv_path = query.get("csv_path", [settings.get("csv_path", str(DEFAULT_CSV_PATH))])[0]
            annotations = get_csv_annotations(csv_path)
            self._send_response_headers(200)
            self.wfile.write(json.dumps(annotations).encode("utf-8"))

        elif path == "/api/settings":
            settings = load_settings()
            self._send_response_headers(200)
            self.wfile.write(json.dumps(settings).encode("utf-8"))

        else:
            if path == "/" or path == "":
                filepath = BASE_DIR / "static" / "index.html"
            else:
                rel = path.lstrip("/")
                filepath = BASE_DIR / rel

            if filepath.exists() and filepath.is_file():
                content_type = "text/html"
                if filepath.suffix == ".css":
                    content_type = "text/css"
                elif filepath.suffix == ".js":
                    content_type = "application/javascript"
                elif filepath.suffix == ".json":
                    content_type = "application/json"
                elif filepath.suffix == ".svg":
                    content_type = "image/svg+xml"
                
                self._send_response_headers(200, content_type)
                with open(filepath, "rb") as f:
                    self.wfile.write(f.read())
            else:
                self.send_error(404, f"File not found: {path}")

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        content_len = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_len).decode("utf-8") if content_len > 0 else "{}"
        
        try:
            data = json.loads(body)
        except Exception:
            data = {}

        if path == "/api/save_annotation":
            settings = load_settings()
            csv_path = data.get("csv_path") or settings.get("csv_path", str(DEFAULT_CSV_PATH))
            annotation = data.get("annotation")

            if not annotation or "annotation_id" not in annotation:
                self._send_response_headers(400)
                self.wfile.write(json.dumps({"error": "Invalid annotation data"}).encode("utf-8"))
                return

            existing = get_csv_annotations(csv_path)
            updated = False
            for i, ann in enumerate(existing):
                if ann.get("annotation_id") == annotation.get("annotation_id"):
                    existing[i] = annotation
                    updated = True
                    break
            if not updated:
                existing.append(annotation)

            save_csv_annotations(csv_path, existing)
            self._send_response_headers(200)
            self.wfile.write(json.dumps({"status": "success", "annotation": annotation, "total_annotations": len(existing)}).encode("utf-8"))

        elif path == "/api/delete_annotation":
            settings = load_settings()
            csv_path = data.get("csv_path") or settings.get("csv_path", str(DEFAULT_CSV_PATH))
            ann_id = data.get("annotation_id")

            existing = get_csv_annotations(csv_path)
            existing = [ann for ann in existing if ann.get("annotation_id") != ann_id]
            save_csv_annotations(csv_path, existing)

            self._send_response_headers(200)
            self.wfile.write(json.dumps({"status": "success", "deleted_id": ann_id, "total_annotations": len(existing)}).encode("utf-8"))

        elif path == "/api/settings":
            settings = load_settings()
            for k, v in data.items():
                settings[k] = v
            save_settings(settings)
            self._send_response_headers(200)
            self.wfile.write(json.dumps({"status": "success", "settings": settings}).encode("utf-8"))

        else:
            self.send_error(404, "Unknown POST endpoint")

def run(port=8888):
    server_address = ("", port)
    httpd = HTTPServer(server_address, AnnotatorHTTPRequestHandler)
    print(f"🚀 Document Annotator Server running at http://localhost:{port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server.")

if __name__ == "__main__":
    port = 8888
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except ValueError:
            pass
    run(port)
