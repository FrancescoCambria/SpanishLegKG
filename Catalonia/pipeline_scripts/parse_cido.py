import os
import re
import sys
import json
import argparse
import tempfile
import requests
from urllib.parse import urlparse, parse_qs
from requests.adapters import HTTPAdapter
from urllib3.util import create_urllib3_context

# Add Catalonia root directory to sys.path to import html_parser
script_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(script_dir)
for d in [script_dir, parent_dir]:
    if d not in sys.path:
        sys.path.append(d)

try:
    from html_parser import parse_document, extract_doc_id_from_url
except ImportError as e:
    print(f"Error importing from html_parser: {e}")
    sys.exit(1)

# Custom SSL adapter to handle legacy connections safely
class CustomSSLAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        context = create_urllib3_context()
        context.set_ciphers('DEFAULT@SECLEVEL=1')
        kwargs['ssl_context'] = context
        return super(CustomSSLAdapter, self).init_poolmanager(*args, **kwargs)

api_session = requests.Session()
api_session.mount('https://', CustomSSLAdapter())
api_session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://cido.diba.cat/"
})

def normalize_title(title):
    if not title:
        return ""
    # Lowercase, replace non-alphanumeric with spaces, collapse spaces
    t = title.lower()
    t = re.sub(r'[^\w\s]', ' ', t)
    t = re.sub(r'\s+', ' ', t).strip()
    
    # Remove common legal prefix prefixes
    prefixes = ['ordre', 'orden', 'resolució', 'resolución', 'anunci', 'anuncio', 'edicte', 'edicto', 'decret', 'decreto', 'llei', 'ley']
    for p in prefixes:
        if t.startswith(p):
            t = t[len(p):].strip()
    return t

def load_dogc_reference_data(data_dir, verbose=False):
    """
    Loads DOGC reference data to construct lookup maps by document ID and normalized title.
    """
    dogc_by_id = {}
    dogc_by_title = {}
    
    backup_path = os.path.join(data_dir, "dogc_documents_2024_2026_backup.json")
    main_path = os.path.join(data_dir, "dogc_documents.json")
    
    target_path = None
    if os.path.exists(backup_path):
        target_path = backup_path
    elif os.path.exists(main_path):
        target_path = main_path
        
    if not target_path:
        if verbose:
            print("Warning: No DOGC reference JSON found in data directory. Overlap check will rely on document ID matching.")
        return dogc_by_id, dogc_by_title

    if verbose:
        print(f"Loading DOGC reference dataset from {target_path}...")
        
    try:
        with open(target_path, "r", encoding="utf-8") as f:
            dogc_list = json.load(f)
            
        for item in dogc_list:
            doc_id = item.get("documentId")
            if doc_id:
                dogc_by_id[str(doc_id)] = item
                
            title = item.get("title")
            if title:
                norm_title = normalize_title(title)
                if norm_title:
                    dogc_by_title[norm_title] = item
                    
        if verbose:
            print(f"Loaded {len(dogc_by_id)} DOGC document IDs and {len(dogc_by_title)} unique normalized titles.")
    except Exception as e:
        print(f"Error loading DOGC reference data: {e}", file=sys.stderr)
        
    return dogc_by_id, dogc_by_title

def markdown_to_simple_html(markdown_text, title="Unknown Title"):
    paragraphs = re.split(r'\n\s*\n', markdown_text)
    html_parts = ["<html><body><div id=\"fullText\">"]
    html_parts.append(f"<h1>{title}</h1>")
    
    for p in paragraphs:
        p_clean = p.strip()
        if not p_clean:
            continue
        p_clean = re.sub(r'^#+\s+', '', p_clean)
        p_clean = re.sub(r'^\*+\s*', '', p_clean)
        p_clean = re.sub(r'^-\s*', '', p_clean)
        p_clean = p_clean.replace("<", "&lt;").replace(">", "&gt;")
        html_parts.append(f"<p>{p_clean}</p>")
        
    html_parts.append("</div></body></html>")
    return "\n".join(html_parts)

def parse_cido_pdf(pdf_url, title, converter, verbose=False):
    """
    Downloads the CIDO PDF, extracts text using Docling, and structures it.
    """
    if verbose:
        print(f"Downloading CIDO PDF: {pdf_url} ...")
        
    try:
        r = api_session.get(pdf_url, timeout=45)
        if r.status_code != 200:
            if verbose:
                print(f"Failed to download CIDO PDF. Status code: {r.status_code}")
            return None
            
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(r.content)
            temp_pdf_path = tmp.name
            
        if verbose:
            print("Converting PDF via Docling document converter...")
        result = converter.convert(temp_pdf_path)
        markdown_text = result.document.export_to_markdown()
        
        try:
            os.remove(temp_pdf_path)
        except Exception:
            pass
            
        if verbose:
            print("Structuring PDF text...")
        html_content = markdown_to_simple_html(markdown_text, title=title)
        parsed = parse_document(html_content, pdf_url)
        return parsed
    except Exception as e:
        if verbose:
            print(f"Error parsing CIDO PDF: {e}")
    return None

def main():
    parser = argparse.ArgumentParser(description="Download and parse Diputacio Barcelona CIDO documents")
    parser.add_argument("--limit", type=int, default=5, help="Number of CIDO normatives to process (default: 5)")
    parser.add_argument("--offset", type=int, default=0, help="Offset to start CIDO queries from (default: 0)")
    parser.add_argument("--cido-id", type=str, default=None, help="Process a single CIDO document by its CIDO ID")
    parser.add_argument("--no-pdf", action="store_true", help="Only parse CIDO metadata and overlap check, skip PDF text extraction")
    parser.add_argument("--verbose", action="store_true", help="Print detailed execution logs")
    args = parser.parse_args()

    cat_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data_dir = os.path.join(cat_root, "data")
    cido_output_dir = os.path.join(data_dir, "cido_structured_output")
    os.makedirs(cido_output_dir, exist_ok=True)
    
    dogc_by_id, dogc_by_title = load_dogc_reference_data(data_dir, verbose=args.verbose)
    
    # Initialize Docling converter if PDF parsing is requested
    converter = None
    if not args.no_pdf:
        if args.verbose:
            print("Initializing Docling converter...")
        try:
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
        except ImportError as e:
            print(f"Docling not available: {e}. Defaulting to metadata-only processing.", file=sys.stderr)
            args.no_pdf = True

    # Query CIDO API
    if args.cido_id:
        cido_url = f"https://api.diba.cat/dadesobertes/cido/v1/normatives-locals/{args.cido_id}"
        print(f"Fetching CIDO normative ID {args.cido_id} from: {cido_url}")
    else:
        cido_url = f"https://api.diba.cat/dadesobertes/cido/v1/normatives-locals?sort=-maxDataPublicacioDocument&page[limit]={args.limit}&page[offset]={args.offset}"
        print(f"Fetching {args.limit} CIDO normatives from: {cido_url}")
    
    try:
        r = api_session.get(cido_url, timeout=15)
        if r.status_code != 200:
            print(f"Error querying CIDO API: HTTP {r.status_code}")
            sys.exit(1)
        cido_response = r.json()
    except Exception as e:
        print(f"Failed to query CIDO API: {e}")
        sys.exit(1)
        
    if args.cido_id:
        records = [cido_response.get("data")] if cido_response.get("data") else []
    else:
        records = cido_response.get("data") or []
    print(f"Retrieved {len(records)} CIDO records. Starting processing...")
    
    success_count = 0
    failed_count = 0
    
    for i, record in enumerate(records, 1):
        cido_id = record.get("id")
        attrs = record.get("attributes") or {}
        title = attrs.get("titol") or "Unknown Title"
        identificator = attrs.get("identificador")
        date_published = attrs.get("maxDataPublicacioDocument")
        institution = attrs.get("institucioDesenvolupat") or "Unknown Institution"
        url_cido = attrs.get("urlCido")
        
        print(f"\n[{i}/{len(records)}] Processing CIDO ID: {cido_id} | {title[:60]}...")
        
        # Load related documents
        rel_docs_url = f"https://api.diba.cat/dadesobertes/cido/v1/normatives-locals/{cido_id}/documents"
        rel_docs = []
        try:
            r_docs = api_session.get(rel_docs_url, timeout=10)
            if r_docs.status_code == 200:
                rel_docs = r_docs.json().get("data") or []
        except Exception as e:
            print(f"  Warning: Failed to fetch related documents for {cido_id}: {e}")
            
        print(f"  Found {len(rel_docs)} associated documents.")
        
        # Check overlaps with DOGC reference data
        overlap_info = {
            "appearsInDogc": False,
            "matchType": None,
            "matchingDogcDocumentId": None,
            "matchingDogcRecord": None
        }
        
        # 1. Direct ID Matching via related document properties
        for rdoc in rel_docs:
            rdoc_attrs = rdoc.get("attributes") or {}
            butlleti = rdoc.get("attributes", {}).get("butlleti")
            
            if butlleti == "DOGC":
                # Extract DOGC ID from urlPdf or urlHtml
                dogc_id = None
                for url_key in ["urlPdf", "urlHtml"]:
                    url = rdoc_attrs.get(url_key)
                    if url:
                        dogc_id = extract_doc_id_from_url(url)
                        if dogc_id:
                            break
                            
                if dogc_id:
                    overlap_info["appearsInDogc"] = True
                    overlap_info["matchType"] = "dogc_document_id"
                    overlap_info["matchingDogcDocumentId"] = dogc_id
                    if dogc_id in dogc_by_id:
                        overlap_info["matchingDogcRecord"] = dogc_by_id[dogc_id]
                    break
                    
        # 2. Fuzzy Title-based Overlap Check (if direct ID matching did not succeed)
        if not overlap_info["appearsInDogc"] and dogc_by_title:
            norm_cido_title = normalize_title(title)
            if norm_cido_title in dogc_by_title:
                matched_item = dogc_by_title[norm_cido_title]
                overlap_info["appearsInDogc"] = True
                overlap_info["matchType"] = "normalized_title_similarity"
                overlap_info["matchingDogcDocumentId"] = matched_item.get("documentId")
                overlap_info["matchingDogcRecord"] = matched_item
                
        if overlap_info["appearsInDogc"]:
            print(f"  --> [DOGC OVERLAP FOUND] Match type: '{overlap_info['matchType']}' | DOGC Document ID: {overlap_info['matchingDogcDocumentId']}")
        else:
            print("  --> No DOGC overlap detected.")
            
        # Parse PDF content if available
        parsed_sections = None
        parsed_attachments = None
        pdf_url = None
        
        if not args.no_pdf and rel_docs:
            # Find the best PDF candidate (prefer the primary phase or one that has a PDF URL)
            best_doc = None
            for rdoc in rel_docs:
                rdoc_attrs = rdoc.get("attributes") or {}
                if rdoc_attrs.get("urlPdf"):
                    best_doc = rdoc
                    break
                    
            if best_doc:
                pdf_url = best_doc["attributes"]["urlPdf"]
                parsed_data = parse_cido_pdf(pdf_url, title, converter, verbose=args.verbose)
                if parsed_data:
                    parsed_sections = parsed_data.get("sections")
                    parsed_attachments = parsed_data.get("attachments")
                    
        # Structure results JSON
        cido_structured_doc = {
            "cidoId": cido_id,
            "identificador": identificator,
            "urlCido": url_cido,
            "title": title,
            "institution": institution,
            "datePublished": date_published,
            "year": attrs.get("any"),
            "isVigent": attrs.get("esVigent"),
            "pdfUrl": pdf_url,
            "overlapCheck": overlap_info,
            "sections": parsed_sections,
            "attachments": parsed_attachments,
            "rawCidoAttributes": attrs,
            "rawAssociatedDocuments": [rd.get("attributes") for rd in rel_docs if rd.get("attributes")]
        }
        
        # Save structured document
        output_filename = f"cido_doc_{cido_id}_structured.json"
        output_filepath = os.path.join(cido_output_dir, output_filename)
        try:
            with open(output_filepath, "w", encoding="utf-8") as out:
                json.dump(cido_structured_doc, out, indent=2, ensure_ascii=False)
            print(f"  Successfully saved structured CIDO output to {output_filepath}")
            success_count += 1
        except Exception as e:
            print(f"  Error saving structured output to {output_filepath}: {e}", file=sys.stderr)
            failed_count += 1
            
    print(f"\nProcessing finished. Success: {success_count}, Failed: {failed_count}.")
    print(f"Structured files are cached in {cido_output_dir}/")

if __name__ == "__main__":
    main()
