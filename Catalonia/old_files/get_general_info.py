import os
import re
import sys
import json
import argparse
import requests
import asyncio
from bs4 import BeautifulSoup
from urllib.parse import urlparse, parse_qs
from concurrent.futures import ThreadPoolExecutor, as_completed
from playwright.async_api import async_playwright
from requests.adapters import HTTPAdapter
from urllib3.util import create_urllib3_context

# Custom SSL Adapter to lower security level to SECLEVEL=1.
# This enables TLS negotiation with the legacy ciphers on portaldogc.gencat.cat
class CustomSSLAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        context = create_urllib3_context()
        context.set_ciphers('DEFAULT@SECLEVEL=1')
        kwargs['ssl_context'] = context
        return super(CustomSSLAdapter, self).init_poolmanager(*args, **kwargs)

# Setup a requests session with the Custom SSL Adapter mounted
session = requests.Session()
session.mount('https://', CustomSSLAdapter())

# Default headers to mimic a normal browser request
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "ca-ES,ca;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"
}

def normalize_title(title):
    """
    Normalizes a title to simplify matching. Lowercases and keeps only letters and numbers.
    """
    if not title:
        return ""
    title = title.lower()
    title = re.sub(r'\s+', ' ', title)
    title = re.sub(r'[^a-z0-9à-üçï]', '', title)
    return title

def extract_type_from_title(title):
    """
    Extracts the document type from the start of the title using heuristics.
    """
    if not title:
        return "Unknown"
    title_upper = title.upper().strip()
    if title_upper.startswith("RESOLUCIÓ") or title_upper.startswith("RESOLUCION"):
        return "Resolució"
    elif title_upper.startswith("EDICTE") or title_upper.startswith("EDICTO"):
        return "Edicte"
    elif title_upper.startswith("ANUNCI") or title_upper.startswith("ANUNCIO"):
        return "Anunci"
    elif title_upper.startswith("ORDRE") or title_upper.startswith("ORDEN"):
        return "Ordre"
    elif title_upper.startswith("DECRET") or title_upper.startswith("DECRETO"):
        if "LLEI" in title_upper[:20] or "LEY" in title_upper[:20]:
            return "Decret llei"
        elif "LEGISLATIU" in title_upper[:25] or "LEGISLATIVO" in title_upper[:25]:
            return "Decret legislatiu"
        return "Decret"
    elif title_upper.startswith("LLEI") or title_upper.startswith("LEY"):
        return "Llei"
    elif title_upper.startswith("CORRECCIÓ D'ERRADES") or title_upper.startswith("CORRECCIONS D'ERRADES"):
        return "Correcció d'errades"
    elif title_upper.startswith("NOTIFICACIÓ") or title_upper.startswith("NOTIFICACION"):
        return "Notificació"
    elif title_upper.startswith("ACORD") or title_upper.startswith("ACUERDO"):
        return "Acord"
    elif title_upper.startswith("CONVENI") or title_upper.startswith("CONVENIO"):
        return "Conveni"
    elif title_upper.startswith("CONVOCATÒRIA") or title_upper.startswith("CONVOCATORIA"):
        return "Convocatòria"
    
    # Fallback to the first word
    words = title.split()
    if words:
        first_word = words[0].strip(",.:;()[]'\"").capitalize()
        if len(first_word) > 2:
            return first_word
    return "Other"

def extract_doc_id_from_url(url):
    """
    If the URL is a direct DOGC link, extract the documentId from the query string.
    """
    parsed = urlparse(url)
    query_params = parse_qs(parsed.query)
    doc_id = query_params.get('documentId', [None])[0]
    return doc_id

async def fetch_document_id_from_eli_async(context, eli_url):
    """
    Fallback method to fetch documentId using Playwright APIRequestContext.
    """
    try:
        response = await context.request.get(eli_url, timeout=15000)
        if response.status == 200:
            text = await response.text()
            soup = BeautifulSoup(text, 'html.parser')
            doc_id_input = soup.find('input', {'id': 'documentIdRequest'})
            if doc_id_input:
                return doc_id_input.get('value')
    except Exception:
        pass
    return None

def fetch_metadata_from_rest_api(doc_id):
    """
    Queries the EADOP REST API for document metadata.
    """
    api_url = "https://portaldogc.gencat.cat/eadop-rest/api/dogc/documentDOGC"
    payload = {"documentId": str(doc_id), "language": "ca"}
    try:
        r = session.post(api_url, data=payload, headers={"Referer": "https://dogc.gencat.cat/"}, timeout=15)
        if r.status_code == 200:
            res_json = r.json()
            doc_data = res_json.get("documentData") or {}
            
            # Extract year from dateDocument (format DD/MM/YYYY)
            date_doc = doc_data.get("dateDocument")
            year = ""
            if date_doc and len(date_doc) >= 10:
                year = date_doc.split("/")[-1]
                
            return {
                "documentId": str(doc_id),
                "dogcUrl": f"https://dogc.gencat.cat/ca/document-del-dogc/?documentId={doc_id}",
                "type": doc_data.get("typeDocument") or "Unknown",
                "year": year or doc_data.get("year") or "",
                "dogcNumber": doc_data.get("numDOGC") or "",
                "dogcSection": doc_data.get("sectionDOGC") or "",
                "organisme": doc_data.get("emissor") or "Unknown Emissor",
                "title": res_json.get("titleDocument") or "Unknown Title"
            }
    except Exception:
        pass
    return None

def fetch_summary_from_api(dogc_num):
    """
    Fetches the EADOP summary for a specific DOGC issue number.
    """
    api_url = "https://portaldogc.gencat.cat/eadop-rest/api/dogc/summaryDOGC"
    payload = {"numDOGC": str(dogc_num), "language": "ca"}
    try:
        r = session.post(api_url, data=payload, headers={"Referer": "https://dogc.gencat.cat/"}, timeout=15)
        if r.status_code == 200:
            res_json = r.json()
            sumaris = res_json.get("sumaris") or []
            if sumaris:
                summary_docs = []
                date_str = sumaris[0].get("dateDOGC") or "" # format DD/MM/YYYY
                year = date_str.split("/")[-1] if len(date_str) >= 10 else ""
                
                for sec in sumaris[0].get("section") or []:
                    sec_title = sec.get("title") or ""
                    for header in sec.get("header", []):
                        organisme = header.get("title") or ""
                        for doc in header.get("document", []):
                            title = doc.get("title") or ""
                            pdf_url = doc.get("linkDownloadDocumentPDF", "")
                            doc_id = None
                            match = re.search(r'documentId=(\d+)', pdf_url)
                            if match:
                                doc_id = match.group(1)
                            if doc_id:
                                summary_docs.append({
                                    "documentId": str(doc_id),
                                    "title": title,
                                    "section": sec_title,
                                    "organisme": organisme,
                                    "year": year
                                })
                return dogc_num, summary_docs
    except Exception:
        pass
    return dogc_num, []

async def enrich_manual_records(records, workers):
    """
    Enriches manual URLs (resolutions, etc.) that aren't mapped via Socrata summaries,
    using Playwright as a fallback.
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(locale="ca-ES")
        
        sem = asyncio.Semaphore(workers)
        
        async def enrich_one(rec):
            async with sem:
                url = rec.get("url")
                doc_id = rec.get("documentId")
                if not doc_id:
                    if "documentId=" in url:
                        doc_id = extract_doc_id_from_url(url)
                    else:
                        doc_id = await fetch_document_id_from_eli_async(context, url)
                if doc_id:
                    loop = asyncio.get_running_loop()
                    api_meta = await loop.run_in_executor(None, fetch_metadata_from_rest_api, doc_id)
                    if api_meta:
                        rec.update(api_meta)
                        rec["documentId"] = doc_id
                        return True
            return False
            
        tasks = [enrich_one(rec) for rec in records]
        results = await asyncio.gather(*tasks)
        await browser.close()
        return sum(1 for r in results if r)

def main():
    parser = argparse.ArgumentParser(description="Full Metadata Crawler & List Updater")
    parser.add_argument("--mode", choices=["update", "bulk"], default="update", 
                        help="update: incremental recent laws (default), bulk: rebuild catalog")
    parser.add_argument("--workers", type=int, default=20, 
                        help="Number of concurrent request threads (default 20)")
    args = parser.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    urls_filepath = os.path.join(script_dir, "law_urls.txt")
    metadata_filepath = os.path.join(script_dir, "law_metadata.json")
    
    # 1. Load existing catalog (keys can be documentId or url)
    catalog_by_id = {}
    catalog_by_url = {}
    
    if os.path.exists(metadata_filepath):
        try:
            with open(metadata_filepath, "r", encoding="utf-8") as f:
                data_list = json.load(f)
                for item in data_list:
                    if item.get("documentId"):
                        catalog_by_id[str(item["documentId"])] = item
                    if item.get("url"):
                        catalog_by_url[item["url"]] = item
        except Exception as e:
            print(f"Warning: Failed to load existing metadata: {e}")
            
    # 2. Fetch Socrata API records
    if args.mode == "update" and (catalog_by_id or catalog_by_url):
        print("Fetching recent Socrata records (UPDATE mode)...")
        socrata_url = "https://analisi.transparenciacatalunya.cat/resource/n6hn-rmy7.json?$limit=2000&$order=data_de_publicaci_del_diari DESC"
    else:
        print("Fetching all Socrata records (BULK mode)...")
        socrata_url = "https://analisi.transparenciacatalunya.cat/resource/n6hn-rmy7.json?$limit=50000"
        catalog_by_id = {}
        catalog_by_url = {}
        
    try:
        r = requests.get(socrata_url, timeout=60)
        if r.status_code != 200:
            print(f"Error: Socrata API returned status code {r.status_code}")
            sys.exit(1)
        socrata_records = r.json()
    except Exception as e:
        print(f"Error: Failed to fetch Socrata records: {e}")
        sys.exit(1)
        
    print(f"Retrieved {len(socrata_records)} records from Socrata.")
    
    # 3. Read manual URLs from law_urls.txt
    manual_urls = []
    if os.path.exists(urls_filepath):
        with open(urls_filepath, "r", encoding="utf-8") as f:
            for line in f:
                u = line.strip()
                if u and u not in catalog_by_url:
                    manual_urls.append(u)
                    
    if manual_urls:
        print(f"Found {len(manual_urls)} manually added URLs in law_urls.txt.")
        
    # 4. Integrate Socrata records into catalog
    new_socrata_count = 0
    for record in socrata_records:
        format_html = record.get("format_html") or {}
        html_url = format_html.get("url") or (record.get("url_ltima_versi_format_html") or {}).get("url")
        if not html_url:
            continue
            
        if html_url not in catalog_by_url:
            doc_type = record.get("rang_de_norma") or "Unknown"
            year = str(record.get("any") or "")
            dogc_num = record.get("n_mero_de_diari") or ""
            title = record.get("t_tol_de_la_norma") or "Unknown Title"
            
            entry = {
                "documentId": None,
                "url": html_url,
                "dogcUrl": None,
                "type": doc_type,
                "year": year,
                "dogcNumber": dogc_num,
                "dogcSection": None,
                "organisme": "Unknown Organisme",
                "title": title
            }
            catalog_by_url[html_url] = entry
            new_socrata_count += 1
        else:
            entry = catalog_by_url[html_url]
            # Update missing Socrata-supplied fields
            if not entry.get("dogcNumber") and record.get("n_mero_de_diari"):
                entry["dogcNumber"] = record.get("n_mero_de_diari")
            if not entry.get("title") and record.get("t_tol_de_la_norma"):
                entry["title"] = record.get("t_tol_de_la_norma")
                
    # Integrate manual URLs
    new_manual_count = 0
    for m_url in manual_urls:
        if m_url not in catalog_by_url:
            entry = {
                "documentId": None,
                "url": m_url,
                "dogcUrl": None,
                "type": "Unknown",
                "year": "",
                "dogcNumber": "",
                "dogcSection": None,
                "organisme": "Unknown Organisme",
                "title": "Unknown Title"
            }
            catalog_by_url[m_url] = entry
            new_manual_count += 1
            
    print(f"Merged catalog: total {len(catalog_by_url)} normative/manual records ({new_socrata_count} new Socrata, {new_manual_count} new manual).")
    
    # 5. Extract unique DOGC numbers that need summaries
    dogc_nums_to_fetch = set()
    for entry in catalog_by_url.values():
        if entry.get("dogcNumber") and (not entry.get("documentId") or not entry.get("dogcSection")):
            dogc_nums_to_fetch.add(str(entry.get("dogcNumber")))
            
    print(f"Found {len(dogc_nums_to_fetch)} unique DOGC issues requiring summary retrieval.")
    
    # 6. Fetch summaries in bulk and integrate ALL documents from the daily gazettes
    all_summary_docs = []
    if dogc_nums_to_fetch:
        print(f"Fetching summaries for {len(dogc_nums_to_fetch)} DOGC issues using {args.workers} threads...")
        processed = 0
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = {executor.submit(fetch_summary_from_api, num): num for num in dogc_nums_to_fetch}
            for future in as_completed(futures):
                num, doc_list = future.result()
                processed += 1
                for doc in doc_list:
                    # Enriched doc info
                    doc["dogcNumber"] = str(num)
                    doc["type"] = extract_type_from_title(doc["title"])
                    doc["dogcUrl"] = f"https://dogc.gencat.cat/ca/document-del-dogc/?documentId={doc['documentId']}"
                    all_summary_docs.append(doc)
                    
                if processed % 100 == 0 or processed == len(dogc_nums_to_fetch):
                    print(f"Summaries Progress: {processed}/{len(dogc_nums_to_fetch)} issues processed.")
                    
        print(f"Summaries fetched. Found a total of {len(all_summary_docs)} documents across daily summaries.")
        
    # 7. Match Socrata/manual records with summary documents by normalized title
    # First build a title lookup dictionary for these issue summaries
    summary_lookup = {}
    for doc in all_summary_docs:
        norm_t = normalize_title(doc["title"])
        summary_lookup[(doc["dogcNumber"], norm_t)] = doc
        
    enriched_count = 0
    for url, entry in catalog_by_url.items():
        if entry.get("dogcNumber") and (not entry.get("documentId") or not entry.get("dogcSection")):
            num = str(entry.get("dogcNumber"))
            norm_t = normalize_title(entry.get("title"))
            
            match = summary_lookup.get((num, norm_t))
            if not match:
                # Substring matching fallback
                for (api_num, api_norm), api_doc in summary_lookup.items():
                    if api_num == num and (norm_t in api_norm or api_norm in norm_t):
                        match = api_doc
                        break
                        
            if match:
                entry["documentId"] = match["documentId"]
                entry["dogcUrl"] = match["dogcUrl"]
                entry["dogcSection"] = match["section"]
                entry["organisme"] = match["organisme"]
                if match["year"]:
                    entry["year"] = match["year"]
                enriched_count += 1
                
                # Link in catalog_by_id
                catalog_by_id[str(match["documentId"])] = entry
                
    print(f"Enriched {enriched_count} existing records in bulk via summary matching.")
    
    # 8. Add all other documents from summaries (resolutions, edicts, etc.) that aren't in the Socrata list
    added_non_normative = 0
    for doc in all_summary_docs:
        doc_id = doc["documentId"]
        # Skip if already exists in catalog
        if doc_id in catalog_by_id:
            continue
            
        # Create a new non-normative catalog entry
        new_entry = {
            "documentId": doc_id,
            "url": None, # No ELI URL for non-normative
            "dogcUrl": doc["dogcUrl"],
            "type": doc["type"],
            "year": doc["year"],
            "dogcNumber": doc["dogcNumber"],
            "dogcSection": doc["section"],
            "organisme": doc["organisme"],
            "title": doc["title"]
        }
        catalog_by_id[doc_id] = new_entry
        # If it has an ELI url (not typical), key it there too; else just keep in ID index
        added_non_normative += 1
        
    print(f"Added {added_non_normative} new non-normative documents (resolutions, edicts, announcements, etc.) to the catalog.")
    
    # 9. Fallback Playwright enrichment for remaining unmatched manual/ELI records
    fallback_records = []
    for entry in catalog_by_url.values():
        if not entry.get("documentId") or not entry.get("dogcSection"):
            fallback_records.append(entry)
            
    print(f"Remaining catalog records requiring fallback: {len(fallback_records)}")
    
    if fallback_records:
        print(f"Enriching {len(fallback_records)} fallback records via Playwright APIRequestContext...")
        enriched_fallback = asyncio.run(enrich_manual_records(fallback_records, 5))
        print(f"Fallback enrichment completed: {enriched_fallback} records enriched.")
        
        # Link newly resolved fallback items into catalog_by_id
        for entry in fallback_records:
            if entry.get("documentId"):
                catalog_by_id[str(entry["documentId"])] = entry
                
    # 10. Compile and write the final catalog list
    # Merge catalog_by_url entries and any catalog_by_id entries not in catalog_by_url
    final_catalog_dict = {}
    # First add all by URL
    for url, entry in catalog_by_url.items():
        if entry.get("documentId"):
            final_catalog_dict[str(entry["documentId"])] = entry
        else:
            final_catalog_dict["url_" + url] = entry
            
    # Then add all other by ID (non-normative items from summaries)
    for doc_id, entry in catalog_by_id.items():
        final_catalog_dict[str(doc_id)] = entry
        
    final_list = list(final_catalog_dict.values())
    
    success_count = sum(1 for e in final_list if e.get("documentId") and e.get("dogcSection"))
    print(f"Catalog Summary: Total Catalog={len(final_list)}, Successfully Enriched={success_count}, Missing={len(final_list)-success_count}")
    
    with open(metadata_filepath, "w", encoding="utf-8") as f:
        json.dump(final_list, f, indent=2, ensure_ascii=False)
    print(f"Metadata list successfully saved to {metadata_filepath}.")
    
    # Save all unique URLs to law_urls.txt
    unique_urls = []
    for entry in final_list:
        best_url = entry.get("dogcUrl") or entry.get("url")
        if best_url:
            unique_urls.append(best_url)
            
    with open(urls_filepath, "w", encoding="utf-8") as f:
        for u in unique_urls:
            f.write(u + "\n")
    print(f"URLs list successfully saved to {urls_filepath} (Total: {len(unique_urls)} URLs).")

if __name__ == "__main__":
    main()
