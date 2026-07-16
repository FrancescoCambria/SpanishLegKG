import os
import re
import sys
import json
import tempfile
import requests
from requests.adapters import HTTPAdapter
from urllib3.util import create_urllib3_context

# Ensure we can import html_parser
script_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(script_dir)
for d in [script_dir, parent_dir]:
    if d not in sys.path:
        sys.path.append(d)
from html_parser import parse_document

# Custom SSL adapter to handle legacy secure connections to portaldogc
class CustomSSLAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        context = create_urllib3_context()
        context.set_ciphers('DEFAULT@SECLEVEL=1')
        kwargs['ssl_context'] = context
        return super(CustomSSLAdapter, self).init_poolmanager(*args, **kwargs)

api_session = requests.Session()
api_session.mount('https://', CustomSSLAdapter())

def find_document_boundaries(text, title, doc_num):
    """
    Given the full extracted text of a PDF issue, identify the start and end of our target law.
    """
    clean_num = doc_num.lstrip('0') if '/' in doc_num else doc_num
    
    # Locate all occurrences of the clean document number
    num_pat = re.compile(rf'\b{re.escape(clean_num)}\b', re.IGNORECASE)
    matches = list(num_pat.finditer(text))
    
    if not matches:
        return 0, len(text)
        
    # Pick the match closest to words from the document title
    words = [w for w in re.findall(r'\w{4,}', title) if w.lower() not in ('ley', 'decreto', 'orden', 'parcial', 'modificación')]
    best_idx = matches[0].start()
    best_score = -1
    for m in matches:
        idx = m.start()
        window = text[max(0, idx-200):idx+500].lower()
        score = sum(1 for w in words if w.lower() in window)
        if score > best_score:
            best_score = score
            best_idx = idx
            
    # Start of document is a bit before the matched document number/title
    start_idx = max(0, best_idx - 100)
    
    # Find the start of the next document (LEY, DECRETO, ORDEN, etc.)
    subtext = text[start_idx:]
    next_doc_pat = re.compile(r'\n\s*#*\s*(?:LEY|DECRETO|ORDEN|RESOLUCIÓN|EDICTO|ANUNCIO|CVE|SUMARIO)\b', re.IGNORECASE)
    end_idx = len(text)
    for m in next_doc_pat.finditer(subtext):
        if m.start() > 800:  # Must be reasonably far from the document start to avoid self-matching
            end_idx = start_idx + m.start()
            break
            
    return start_idx, end_idx

def markdown_to_simple_html(markdown_text, title="Unknown Title"):
    # Split by double newlines to separate paragraphs
    paragraphs = re.split(r'\n\s*\n', markdown_text)
    
    html_parts = ["<html><body><div id=\"fullText\">"]
    # Add h1 title
    html_parts.append(f"<h1>{title}</h1>")
    
    for p in paragraphs:
        p_clean = p.strip()
        if not p_clean:
            continue
        # Clean markdown elements (like list bullet points, headers, bold markup)
        p_clean = re.sub(r'^#+\s+', '', p_clean)
        p_clean = re.sub(r'^\*+\s*', '', p_clean)
        p_clean = re.sub(r'^-\s*', '', p_clean)
        # Escape HTML characters to avoid parsing errors
        p_clean = p_clean.replace("<", "&lt;").replace(">", "&gt;")
        html_parts.append(f"<p>{p_clean}</p>")
        
    html_parts.append("</div></body></html>")
    return "\n".join(html_parts)

def main():
    script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    output_dir = os.path.join(script_dir, "data", "structured_output")
    if not os.path.exists(output_dir):
        print(f"Error: Output directory {output_dir} does not exist.")
        sys.exit(1)
        
    files = sorted([f for f in os.listdir(output_dir) if f.endswith("_structured.json")])
    
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions, TesseractCliOcrOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption
    
    pipeline_options = PdfPipelineOptions()
    pipeline_options.do_ocr = True
    pipeline_options.ocr_options = TesseractCliOcrOptions()
    converter = DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
        }
    )
    
    success_count = 0
    skipped_count = 0
    failed_count = 0
    
    for idx, filename in enumerate(files, 1):
        filepath = os.path.join(output_dir, filename)
        with open(filepath, "r", encoding="utf-8") as f:
            doc = json.load(f)
            
        doc_id = doc.get("documentId")
        ca = doc.get("ca")
        es = doc.get("es")
        
        # Check if Spanish version exists
        if not es:
            skipped_count += 1
            continue
            
        # Determine if it's a legacy document only available in PDF format
        is_legacy = False
        add_text = es.get("additionalText") or ""
        if "PDF" in add_text or "pdf" in add_text or "versió catalana" in add_text.lower():
            is_legacy = True
            
        if not is_legacy:
            skipped_count += 1
            continue
            
        # Locate PDF URL
        pdf_url = es.get("formats", {}).get("pdf")
        if not pdf_url:
            print(f"[{idx}/{len(files)}] {filename}: Legacy document has no Spanish PDF URL, skipping.")
            skipped_count += 1
            continue
            
        print(f"\n[{idx}/{len(files)}] Processing {filename} (ID: {doc_id})...")
        print(f"--> Downloading PDF: {pdf_url} ...")
        
        try:
            r_pdf = api_session.get(pdf_url, timeout=45)
            if r_pdf.status_code != 200:
                print(f"--> [Error] Failed to download PDF (Status code: {r_pdf.status_code})")
                failed_count += 1
                continue
                
            # Write to temp file
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                tmp.write(r_pdf.content)
                temp_pdf_path = tmp.name
                
            print("--> Converting PDF via Docling...")
            result = converter.convert(temp_pdf_path)
            markdown_text = result.document.export_to_markdown()
            
            # Clean up temp file
            try:
                os.remove(temp_pdf_path)
            except Exception:
                pass
                
            print("--> Isolating target document text...")
            spanish_title = es.get("title") or "Unknown Title"
            doc_num = ca.get("metadata", {}).get("documentNumber") or ""
            
            start_idx, end_idx = find_document_boundaries(markdown_text, spanish_title, doc_num)
            cropped_text = markdown_text[start_idx:end_idx]
            
            print("--> Parsing extracted markdown text...")
            html_content = markdown_to_simple_html(cropped_text, title=spanish_title)
            
            parsed_doc = parse_document(html_content, es["url"])
            if parsed_doc:
                es["sections"] = parsed_doc.get("sections")
                es["attachments"] = parsed_doc.get("attachments")
                
                # Write updated structure back to file
                with open(filepath, "w", encoding="utf-8") as out:
                    json.dump(doc, out, indent=2, ensure_ascii=False)
                print(f"--> [Success] Extracted and saved Spanish text for {filename}!")
                success_count += 1
            else:
                print("--> [Error] Failed to parse converted HTML blocks.")
                failed_count += 1
                
        except Exception as e:
            print(f"--> [Error] Failed to extract text for {filename}: {e}")
            failed_count += 1
            
    print(f"\nPDF Extraction finished. Success: {success_count}, Skipped: {skipped_count}, Failed: {failed_count}.")

if __name__ == "__main__":
    main()
