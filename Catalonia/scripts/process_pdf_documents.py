import os
import re
import sys
import json
import tempfile
import argparse
import requests
from bs4 import BeautifulSoup
from urllib3.util import create_urllib3_context
from requests.adapters import HTTPAdapter

# Add Catalonia root & pipeline_scripts to sys.path
script_dir = os.path.dirname(os.path.abspath(__file__))
cat_root = os.path.dirname(script_dir)
for d in [script_dir, cat_root]:
    if d not in sys.path:
        sys.path.append(d)

try:
    from html_parser import (
        CHAPTER_PAT, ARTICLE_PAT, DISPOSITION_PAT, ANNEX_PAT, SIGNATURE_START_PAT, RESOL_POINT_PAT,
        parse_document
    )
except ImportError as e:
    print(f"Warning: Could not import html_parser utilities: {e}", file=sys.stderr)
    CHAPTER_PAT = re.compile(
        r'^\s*(Capítol|Capítulo|Títol|Título|Secció|Sección)\s+(preliminar|[I|V|X|L|C]+|\d+[\w\-]*)\.?\s*(.*)',
        re.IGNORECASE
    )

# Custom SSL Adapter for legacy SSL/TLS ciphers on Catalan servers
class CustomSSLAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        try:
            from urllib3.util import create_urllib3_context
        except ImportError:
            from urllib3.util.ssl_ import create_urllib3_context
        context = create_urllib3_context()
        context.set_ciphers('DEFAULT@SECLEVEL=1')
        kwargs['ssl_context'] = context
        return super(CustomSSLAdapter, self).init_poolmanager(*args, **kwargs)

def setup_session():
    session = requests.Session()
    session.mount('https://', CustomSSLAdapter())
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://cido.diba.cat/"
    })
    return session

def download_pdf(session, pdf_url, verbose=False):
    """
    Downloads a PDF document from URL to a temporary file.
    """
    if verbose:
        print(f"Downloading PDF: {pdf_url} ...")
    try:
        r = session.get(pdf_url, timeout=45)
        if r.status_code == 200 and r.content:
            tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
            tmp.write(r.content)
            tmp.close()
            return tmp.name
        else:
            if verbose:
                print(f"Failed to download PDF. HTTP status: {r.status_code}")
    except Exception as e:
        if verbose:
            print(f"Error downloading PDF from {pdf_url}: {e}")
    return None

def extract_markdown_from_pdf(pdf_path, converter=None, verbose=False):
    """
    Extracts text/markdown from PDF using Docling converter if available.
    """
    if converter:
        if verbose:
            print("Converting PDF using Docling DocumentConverter...")
        try:
            result = converter.convert(pdf_path)
            markdown_text = result.document.export_to_markdown()
            if markdown_text and markdown_text.strip():
                return markdown_text
        except Exception as e:
            if verbose:
                print(f"Docling conversion error: {e}. Trying fallback text extraction...")

    # Fallback basic text extraction if docling fails or is not provided
    try:
        import pypdf
        reader = pypdf.PdfReader(pdf_path)
        pages_text = []
        for i, page in enumerate(reader.pages):
            txt = page.extract_text()
            if txt:
                pages_text.append(txt)
        return "\n\n".join(pages_text)
    except Exception:
        pass

    return ""

def markdown_to_html_blocks(markdown_text, title="Document"):
    """
    Converts markdown lines into structured HTML paragraphs and headings
    suitable for BeautifulSoup parsing.
    """
    lines = markdown_text.splitlines()
    html_parts = [
        "<!DOCTYPE html>",
        "<html><head><meta charset='utf-8'><title>",
        title.replace("<", "&lt;").replace(">", "&gt;"),
        "</title></head><body><div class='body-text'>"
    ]
    
    current_para = []
    
    def flush_para():
        if current_para:
            text = " ".join(current_para).strip()
            text = text.replace("<", "&lt;").replace(">", "&gt;")
            if text:
                html_parts.append(f"<p>{text}</p>")
            current_para.clear()

    for line in lines:
        stripped = line.strip()
        if not stripped:
            flush_para()
            continue
            
        if stripped.startswith("#"):
            flush_para()
            h_text = re.sub(r'^#+\s*', '', stripped)
            h_text = h_text.replace("<", "&lt;").replace(">", "&gt;")
            html_parts.append(f"<h2>{h_text}</h2>")
        else:
            p_clean = re.sub(r'^\*+\s*', '', stripped)
            p_clean = re.sub(r'^-\s*', '', p_clean)
            current_para.append(p_clean)
            
    flush_para()
    html_parts.append("</div></body></html>")
    return "\n".join(html_parts)

def parse_pdf_to_sections(pdf_url, markdown_text, title="Document", verbose=False):
    """
    Parses PDF text into structured sections using BeautifulSoup & regex patterns.
    """
    html_content = markdown_to_html_blocks(markdown_text, title=title)
    soup = BeautifulSoup(html_content, 'html.parser')
    
    # Use html_parser parse_document if available
    try:
        parsed_doc = parse_document(html_content, pdf_url)
        if parsed_doc and parsed_doc.get("sections"):
            return parsed_doc
    except Exception as e:
        if verbose:
            print(f"Warning: html_parser.parse_document encountered error: {e}. Falling back to BeautifulSoup parsing.")

    # Manual BeautifulSoup section segmentation fallback
    sections = []
    current_chapter = None
    
    current_section = {
        "type": "introduction",
        "title": "Preamble / Introduction",
        "heading": None,
        "chapter": None,
        "commas": []
    }
    sections.append(current_section)
    
    paragraphs = soup.find_all(['p', 'h1', 'h2', 'h3'])
    for p in paragraphs:
        text = p.get_text().strip()
        if not text:
            continue
            
        if CHAPTER_PAT.match(text):
            m = CHAPTER_PAT.match(text)
            chap_type = m.group(1).capitalize()
            chap_num = m.group(2)
            heading = m.group(3)
            chap_title = f"{chap_type} {chap_num}"
            current_chapter = f"{chap_title}. {heading}".strip(". ") if heading else chap_title
            current_section = {
                "type": "chapter",
                "title": chap_title,
                "heading": heading or None,
                "chapter": current_chapter,
                "commas": []
            }
            sections.append(current_section)
        elif ARTICLE_PAT.match(text):
            m = ARTICLE_PAT.match(text)
            art_num = m.group(2)
            art_rest = m.group(3)
            current_section = {
                "type": "article",
                "title": f"Article {art_num}",
                "heading": art_rest if art_rest else None,
                "chapter": current_chapter,
                "commas": []
            }
            sections.append(current_section)
        elif DISPOSITION_PAT.match(text):
            m = DISPOSITION_PAT.match(text)
            disp_type = m.group(2)
            disp_num = m.group(3)
            current_section = {
                "type": "disposition",
                "title": f"Disposició {disp_type} {disp_num}".strip(),
                "heading": m.group(4) or None,
                "chapter": current_chapter,
                "commas": []
            }
            sections.append(current_section)
        elif ANNEX_PAT.match(text):
            m = ANNEX_PAT.match(text)
            annex_num = m.group(2) or ""
            current_section = {
                "type": "annex",
                "title": f"Annex {annex_num}".strip(),
                "heading": m.group(3) or None,
                "chapter": current_chapter,
                "commas": []
            }
            sections.append(current_section)
        elif SIGNATURE_START_PAT.match(text):
            current_section = {
                "type": "signature",
                "title": "Signatures",
                "heading": None,
                "chapter": current_chapter,
                "commas": [text]
            }
            sections.append(current_section)
        else:
            current_section["commas"].append(text)
            
    # Clean empty introduction if redundant
    if len(sections) > 1 and sections[0]["type"] == "introduction" and not sections[0]["commas"]:
        sections.pop(0)

    return {
        "url": pdf_url,
        "title": title,
        "sections": sections
    }

def init_docling_converter(verbose=False):
    """
    Initializes Docling DocumentConverter.
    """
    try:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption
        
        if verbose:
            print("Initializing Docling DocumentConverter engine...")
        pipeline_options = PdfPipelineOptions()
        pipeline_options.do_ocr = False # set to True if OCR scanning is needed
        return DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
            }
        )
    except Exception as e:
        if verbose:
            print(f"Docling converter initialization warning: {e}")
        return None

def process_single_pdf(session, pdf_url, title="Unknown Document", converter=None, verbose=False):
    """
    Downloads, converts, and parses a single PDF into structured sections.
    """
    temp_pdf_path = download_pdf(session, pdf_url, verbose=verbose)
    if not temp_pdf_path:
        return None
        
    try:
        markdown_text = extract_markdown_from_pdf(temp_pdf_path, converter=converter, verbose=verbose)
        parsed_result = parse_pdf_to_sections(pdf_url, markdown_text, title=title, verbose=verbose)
        parsed_result["fullTextMarkdown"] = markdown_text
        return parsed_result
    finally:
        try:
            os.remove(temp_pdf_path)
        except Exception:
            pass

def main():
    parser = argparse.ArgumentParser(description="Download and parse PDF documents into structured sections using BeautifulSoup & Docling")
    parser.add_argument("--pdf-url", type=str, default=None, help="Direct URL of a PDF to process")
    parser.add_argument("--cido-id", type=str, default=None, help="Process PDF documents for a specific CIDO ID from cido_to_dogc_map.json")
    parser.add_argument("--limit", type=int, default=5, help="Number of PDF documents to process in batch mode (default: 5)")
    parser.add_argument("--output-dir", type=str, default="data/pdf_structured_output", help="Directory to save structured JSON outputs")
    parser.add_argument("--no-docling", action="store_true", help="Disable Docling converter; use lightweight text extraction")
    parser.add_argument("--verbose", action="store_true", help="Print verbose execution progress")
    args = parser.parse_args()

    output_dir = os.path.join(cat_root, args.output_dir)
    os.makedirs(output_dir, exist_ok=True)
    
    session = setup_session()
    converter = None if args.no_docling else init_docling_converter(verbose=args.verbose)
    
    # Mode 1: Single PDF URL provided directly
    if args.pdf_url:
        print(f"\nProcessing single PDF URL: {args.pdf_url}")
        parsed = process_single_pdf(session, args.pdf_url, title="PDF Document", converter=converter, verbose=True)
        if parsed:
            out_name = f"pdf_doc_{abs(hash(args.pdf_url))}.json"
            out_path = os.path.join(output_dir, out_name)
            with open(out_path, "w", encoding="utf-8") as out:
                json.dump(parsed, out, indent=2, ensure_ascii=False)
            print(f"Successfully saved structured PDF output to: {out_path}")
            print(f"Total sections extracted: {len(parsed.get('sections', []))}")
        else:
            print("Failed to process PDF URL.")
        return

    # Mode 2: Batch process from cido_to_dogc_map.json
    map_json_path = os.path.join(cat_root, "data", "cido_to_dogc_map.json")
    if not os.path.exists(map_json_path):
        print(f"Error: {map_json_path} not found. Please provide --pdf-url or generate mapping JSON first.")
        sys.exit(1)
        
    print(f"\nLoading mapping records from {map_json_path}...")
    with open(map_json_path, "r", encoding="utf-8") as f:
        cido_mappings = json.load(f)
        
    targets = []
    for record in cido_mappings:
        c_id = record.get("cidoId")
        if args.cido_id and str(c_id) != str(args.cido_id):
            continue
            
        r_title = record.get("title") or "CIDO Record"
        docs = record.get("documents") or []
        for d in docs:
            pdf = d.get("urlPdf")
            if pdf and pdf.strip():
                targets.append({
                    "cidoId": c_id,
                    "title": r_title,
                    "fase": d.get("fase"),
                    "pdfUrl": pdf
                })
                if len(targets) >= args.limit and not args.cido_id:
                    break
        if len(targets) >= args.limit and not args.cido_id:
            break
            
    print(f"Found {len(targets)} candidate PDF documents to process.")
    processed_count = 0
    
    for i, target in enumerate(targets, 1):
        pdf_url = target["pdfUrl"]
        c_id = target["cidoId"]
        doc_title = target["title"]
        fase = target["fase"]
        
        print(f"\n[{i}/{len(targets)}] Processing CIDO {c_id} ({fase}) | {doc_title[:50]}...")
        parsed = process_single_pdf(session, pdf_url, title=doc_title, converter=converter, verbose=args.verbose)
        
        if parsed:
            output_data = {
                "cidoId": c_id,
                "fase": fase,
                "docTitle": doc_title,
                "pdfUrl": pdf_url,
                "sections": parsed.get("sections", []),
                "fullTextMarkdown": parsed.get("fullTextMarkdown", "")
            }
            out_name = f"cido_{c_id}_{fase.replace(' ', '_').lower()}_structured.json"
            # sanitize filename
            out_name = re.sub(r'[^\w\.\-]', '_', out_name)
            out_path = os.path.join(output_dir, out_name)
            
            with open(out_path, "w", encoding="utf-8") as out:
                json.dump(output_data, out, indent=2, ensure_ascii=False)
            print(f"  Successfully parsed & saved: {out_name} ({len(parsed.get('sections', []))} sections)")
            processed_count += 1
        else:
            print(f"  Failed to process PDF: {pdf_url}")
            
    print(f"\nBatch processing complete. Successfully processed {processed_count}/{len(targets)} PDF documents.")
    print(f"Structured JSON files saved to: {output_dir}/")

if __name__ == "__main__":
    main()
