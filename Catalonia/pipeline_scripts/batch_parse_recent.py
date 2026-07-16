import os
import sys
import json
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm

script_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(script_dir)
for d in [script_dir, parent_dir]:
    if d not in sys.path:
        sys.path.append(d)

from html_parser import fetch_document_from_rest_api, get_spanish_url

output_dir = os.path.join(script_dir, "structured_output")
os.makedirs(output_dir, exist_ok=True)

def process_single_doc(doc):
    doc_id = doc.get("documentId")
    if not doc_id:
        return False
        
    # Check if already processed
    output_filename = f"dogc_doc_{doc_id}_structured.json"
    output_filepath = os.path.join(output_dir, output_filename)
    if os.path.exists(output_filepath):
        return True
        
    url_ca = doc.get("htmlUrl")
    if not url_ca:
        url_ca = f"https://dogc.gencat.cat/ca/document-del-dogc/index.html?documentId={doc_id}"
        
    url_es = get_spanish_url(url_ca)
    
    try:
        parsed_ca = fetch_document_from_rest_api(doc_id, url_ca, language="ca")
        if not parsed_ca:
            return False
            
        parsed_es = None
        try:
            parsed_es = fetch_document_from_rest_api(doc_id, url_es, language="es")
        except Exception:
            pass
            
        bilingual_data = {
            "documentId": doc_id,
            "eliUri": parsed_ca.get("eliUri") or (parsed_es.get("eliUri") if parsed_es else ""),
            "ca": {
                "url": parsed_ca.get("url"),
                "title": parsed_ca.get("title"),
                "formats": parsed_ca.get("formats"),
                "metadata": parsed_ca.get("metadata"),
                "additionalText": parsed_ca.get("additionalText"),
                "affectations": parsed_ca.get("affectations"),
                "descriptors": parsed_ca.get("descriptors"),
                "sections": parsed_ca.get("sections"),
                "attachments": parsed_ca.get("attachments")
            },
            "es": {
                "url": parsed_es.get("url") if parsed_es else url_es,
                "title": parsed_es.get("title") if parsed_es else None,
                "formats": parsed_es.get("formats") if parsed_es else None,
                "metadata": parsed_es.get("metadata") if parsed_es else None,
                "additionalText": parsed_es.get("additionalText") if parsed_es else None,
                "affectations": parsed_es.get("affectations") if parsed_es else None,
                "descriptors": parsed_es.get("descriptors") if parsed_es else None,
                "sections": parsed_es.get("sections") if parsed_es else None,
                "attachments": parsed_es.get("attachments") if parsed_es else None
            } if parsed_es else None
        }
        
        with open(output_filepath, "w", encoding="utf-8") as out:
            json.dump(bilingual_data, out, indent=2, ensure_ascii=False)
        return True
    except Exception:
        return False

def main():
    parser = argparse.ArgumentParser(description="Batch parse recent documents from DOGC dataset")
    parser.add_argument("--years", type=str, default="2024,2025,2026", help="Comma-separated list of years to parse (default: 2024,2025,2026)")
    parser.add_argument("--workers", type=int, default=25, help="Number of concurrent thread workers (default: 25)")
    args = parser.parse_args()

    dogc_json_path = os.path.join(script_dir, "dogc_documents.json")
    print(f"Loading base documents from {dogc_json_path}...")
    with open(dogc_json_path, "r", encoding="utf-8") as f:
        docs = json.load(f)
        
    year_list = [y.strip() for y in args.years.split(",") if y.strip()]
    recent_docs = [d for d in docs if str(d.get("year") or "") in year_list]
    print(f"Selected {len(recent_docs)} documents matching years: {year_list}")
    
    # Pre-check how many are already processed
    to_process = []
    skipped_count = 0
    for doc in recent_docs:
        doc_id = doc.get("documentId")
        output_filename = f"dogc_doc_{doc_id}_structured.json"
        output_filepath = os.path.join(output_dir, output_filename)
        if os.path.exists(output_filepath):
            skipped_count += 1
        else:
            to_process.append(doc)
            
    print(f"Already processed: {skipped_count}. To process: {len(to_process)}")
    
    if not to_process:
        print("All documents already structured. Done!")
        return

    success_count = 0
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(process_single_doc, doc): doc for doc in to_process}
        
        # Wrap in tqdm to show progress bar
        for future in tqdm(as_completed(futures), total=len(futures), desc="Parsing documents"):
            if future.result():
                success_count += 1
                
    print(f"\nFinished. Success: {success_count}, Skipped: {skipped_count}, Total: {len(recent_docs)}")

if __name__ == "__main__":
    main()
