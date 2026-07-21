import os
import re
import sys
import json
import time
import argparse
import requests
try:
    from urllib3.util import create_urllib3_context
except ImportError:
    from urllib3.util.ssl_ import create_urllib3_context
from requests.adapters import HTTPAdapter

# Custom SSL Adapter to lower security level to SECLEVEL=1.
# This enables TLS negotiation with the legacy ciphers on portaldogc.gencat.cat
class CustomSSLAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        context = create_urllib3_context()
        context.set_ciphers('DEFAULT@SECLEVEL=1')
        kwargs['ssl_context'] = context
        return super(CustomSSLAdapter, self).init_poolmanager(*args, **kwargs)

def setup_session():
    session = requests.Session()
    session.mount('https://', CustomSSLAdapter())
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://dogc.gencat.cat/"
    })
    return session

def fetch_summary_from_api(session, dogc_num, max_retries=3):
    """
    Fetches the EADOP summary for a specific DOGC issue number with retries.
    """
    api_url = "https://portaldogc.gencat.cat/eadop-rest/api/dogc/summaryDOGC"
    payload = {"numDOGC": str(dogc_num), "language": "ca"}
    
    backoff = 1.0
    for attempt in range(max_retries):
        try:
            r = session.post(api_url, data=payload, timeout=20)
            if r.status_code == 200:
                return r.json()
            else:
                print(f"\n[Error] API returned status code {r.status_code} for DOGC {dogc_num} (Attempt {attempt+1}/{max_retries})")
        except Exception as e:
            print(f"\n[Error] Failed to fetch DOGC {dogc_num} on attempt {attempt+1}/{max_retries}: {e}")
            
        if attempt < max_retries - 1:
            time.sleep(backoff)
            backoff *= 2.0
            
    return None

def extract_docs_recursive(data, current_organisme="", current_section=""):
    docs = []
    if isinstance(data, dict):
        title = data.get("title") or ""
        sec_val = title if data.get("section") or ("numDOGC" in data and not current_section) else current_section
        
        doc_list = data.get("document")
        if doc_list:
            org = title if title else current_organisme
            for doc in doc_list:
                docs.append((doc, org, sec_val))
                
        for child_key in ["section", "header", "subheader"]:
            children = data.get(child_key)
            if children:
                rec_sec = title if child_key == "section" else sec_val
                if isinstance(children, list):
                    for child in children:
                        docs.extend(extract_docs_recursive(child, title or current_organisme, rec_sec))
                elif isinstance(children, dict):
                    docs.extend(extract_docs_recursive(children, title or current_organisme, rec_sec))
    return docs

def extract_documents_from_summary(summary_data):
    """
    Parses EADOP REST API summary JSON and returns a list of document dicts recursively.
    """
    sumaris = summary_data.get("sumaris") or []
    if not sumaris:
        return None, []
        
    s = sumaris[0]
    next_num = s.get("laterNumDOGC")
    date_doc = s.get("dateDOGC") or "" # format DD/MM/YYYY
    year = date_doc.split("/")[-1] if len(date_doc) >= 10 else ""
    dogc_num = str(s.get("numDOGC") or "")
    
    docs_found = extract_docs_recursive(s)
    
    docs_list = []
    for doc, org, sec_title in docs_found:
        title = doc.get("title") or ""
        doc_id = None
        
        # Try to extract document ID from PDF URL
        pdf_url = doc.get("linkDownloadDocumentPDF", "")
        match = re.search(r'documentId=(\d+)', pdf_url)
        if match:
            doc_id = match.group(1)
        
        # HTML url if available, otherwise construct standard document page URL
        html_url = doc.get("linkDownloadDocumentHTML")
        if not html_url or html_url == "None":
            if doc_id:
                html_url = f"https://dogc.gencat.cat/ca/document-del-dogc/index.html?documentId={doc_id}"
            else:
                html_url = None
            
        docs_list.append({
            "documentId": doc_id,
            "dogcNumber": dogc_num,
            "dateDOGC": date_doc,
            "year": year,
            "section": sec_title or "DISPOSICIONS",
            "organisme": org or "Unknown",
            "title": title,
            "pdfUrl": pdf_url or None,
            "htmlUrl": html_url
        })
        
    return next_num, docs_list

def load_checkpoint(checkpoint_file, default_start):
    if os.path.exists(checkpoint_file):
        try:
            with open(checkpoint_file, "r") as f:
                state = json.load(f)
                return state.get("next_dogc", default_start), state.get("processed_count", 0)
        except Exception:
            pass
    return default_start, 0

def save_checkpoint(checkpoint_file, next_dogc, processed_count):
    try:
        with open(checkpoint_file, "w") as f:
            json.dump({"next_dogc": next_dogc, "processed_count": processed_count}, f)
    except Exception as e:
        print(f"Failed to write checkpoint file: {e}")

def main():
    parser = argparse.ArgumentParser(description="DOGC Document Scraper - Chronological Web Crawler")
    parser.add_argument("--start", type=str, default="1", help="DOGC number to start crawling from (default: 1)")
    parser.add_argument("--limit", type=int, default=None, help="Maximum number of DOGC issues to crawl in this run")
    parser.add_argument("--output", type=str, default=None, help="Path to output JSON file (defaults to data/dogc_documents.json)")
    parser.add_argument("--delay", type=float, default=0.1, help="Delay between API requests in seconds (default: 0.1)")
    parser.add_argument("--no-resume", action="store_true", help="Do not resume from checkpoint; start fresh")
    args = parser.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    
    if args.output is None:
        data_dir = os.path.join(script_dir, "data")
        os.makedirs(data_dir, exist_ok=True)
        output_filepath = os.path.join(data_dir, "dogc_documents.json")
        output_name_for_print = "data/dogc_documents.json"
    else:
        output_filepath = os.path.abspath(args.output)
        output_name_for_print = args.output
        
    checkpoint_filename = f".{os.path.basename(output_filepath)}.checkpoint"
    checkpoint_filepath = os.path.join(os.path.dirname(output_filepath), checkpoint_filename)

    # Load existing documents if they exist and we are resuming
    existing_docs = []
    if os.path.exists(output_filepath) and not args.no_resume:
        try:
            with open(output_filepath, "r", encoding="utf-8") as f:
                existing_docs = json.load(f)
            print(f"Loaded {len(existing_docs)} documents from existing {output_name_for_print}")
        except Exception as e:
            print(f"Warning: Could not load existing output file: {e}")

    # Determine starting DOGC issue
    start_dogc = args.start
    processed_issues = 0
    skipped_filepath = os.path.join(os.path.dirname(output_filepath), "skipped_dogc_issues.txt")
    
    if not args.no_resume:
        start_dogc, processed_issues = load_checkpoint(checkpoint_filepath, args.start)
        print(f"Resuming crawl from DOGC issue {start_dogc} (already crawled {processed_issues} issues)")
    else:
        # If starting fresh, remove checkpoint and old skipped file if they exist
        if os.path.exists(checkpoint_filepath):
            try:
                os.remove(checkpoint_filepath)
            except Exception:
                pass
        if os.path.exists(skipped_filepath):
            try:
                os.remove(skipped_filepath)
            except Exception:
                pass

    session = setup_session()
    current_dogc = start_dogc
    issues_crawled_this_run = 0
    new_docs_added = 0

    print(f"Starting crawl. Output will be saved to {output_filepath}")
    
    try:
        while current_dogc:
            # Check limit
            if args.limit and issues_crawled_this_run >= args.limit:
                print(f"\nLimit of {args.limit} issues reached. Stopping.")
                break

            print(f"\rCrawling DOGC issue: {current_dogc}...", end="", flush=True)

            summary_data = fetch_summary_from_api(session, current_dogc)
            if not summary_data:
                print(f"\n[Warning] Skipping issue {current_dogc} due to retrieval errors.")
                # Save skipped issue number to file
                try:
                    with open(skipped_filepath, "a", encoding="utf-8") as sf:
                        sf.write(f"{current_dogc}\n")
                except Exception as se:
                    print(f"Failed to log skipped issue {current_dogc}: {se}")
                
                # If we don't get the data, we don't know the exact next issue.
                # We can try to guess by incrementing if it is numeric
                if current_dogc.isdigit():
                    current_dogc = str(int(current_dogc) + 1)
                    continue
                else:
                    print("Cannot proceed chronologically from non-numeric issue name. Exiting.")
                    break

            next_dogc, docs = extract_documents_from_summary(summary_data)
            
            if docs:
                existing_docs.extend(docs)
                new_docs_added += len(docs)

            issues_crawled_this_run += 1
            processed_issues += 1

            # Save progress incrementally every 10 issues
            if issues_crawled_this_run % 10 == 0:
                with open(output_filepath, "w", encoding="utf-8") as f:
                    json.dump(existing_docs, f, indent=2, ensure_ascii=False)
                save_checkpoint(checkpoint_filepath, next_dogc, processed_issues)

            if not next_dogc:
                print(f"\nReached the end of the chronological list at DOGC {current_dogc}!")
                break

            current_dogc = str(next_dogc)
            
            # Politeness delay
            if args.delay > 0:
                time.sleep(args.delay)

    except KeyboardInterrupt:
        print("\nCrawl interrupted by user.")
    finally:
        # Final save
        if new_docs_added > 0:
            with open(output_filepath, "w", encoding="utf-8") as f:
                json.dump(existing_docs, f, indent=2, ensure_ascii=False)
            save_checkpoint(checkpoint_filepath, current_dogc, processed_issues)
            print(f"\nCrawl complete. Added {new_docs_added} new documents across {issues_crawled_this_run} issues.")
            print(f"Total documents in {output_name_for_print}: {len(existing_docs)}")
        else:
            print("\nNo new documents were added.")

if __name__ == "__main__":
    main()
