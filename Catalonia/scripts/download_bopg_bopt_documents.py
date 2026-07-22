import os
import sys
import json
import csv
import io
import time
import requests

def get_link_to_text(html_url, pdf_url, fallback=None):
    if html_url and isinstance(html_url, str) and html_url.strip():
        return html_url.strip()
    if pdf_url and isinstance(pdf_url, str) and pdf_url.strip():
        return pdf_url.strip()
    if fallback and isinstance(fallback, str) and fallback.strip():
        return fallback.strip()
    return None

def fetch_ckan_resources(api_session):
    """
    Fetches CKAN open data resources from dadesobertes.seu-e.cat API.
    """
    base_url = "https://dadesobertes.seu-e.cat/api/3/action/"
    documents = []
    
    # Target dataset CSV resources on dadesobertes.seu-e.cat
    csv_datasets = [
        ("https://dadesobertes.seu-e.cat/csv/agn-n-ordenances-fiscals.csv", "normativa-fiscals"),
        ("https://dadesobertes.seu-e.cat/csv/agn-n-ordenances-reguladores-i-reglaments.csv", "normativa-reguladores")
    ]
    
    for url, category in csv_datasets:
        try:
            r = api_session.get(url, timeout=20)
            if r.status_code == 200:
                content = r.content.decode("utf-8-sig", errors="replace")
                reader = csv.DictReader(io.StringIO(content))
                for row in reader:
                    ens = row.get("NOM_ENS", "")
                    title = row.get("RESUM", "")
                    link = row.get("ENLLAÇ", "")
                    dt = row.get("DATA_PUB", "")
                    vigent = row.get("VIGENT")
                    
                    ens_lower = ens.lower()
                    link_lower = link.lower()
                    
                    bulletin = None
                    if "girona" in ens_lower or "bopg" in link_lower or "ddgi" in link_lower:
                        bulletin = "BOPG"
                    elif "tarragona" in ens_lower or "bopt" in link_lower or "diputaciodetarragona" in link_lower:
                        bulletin = "BOPT"
                        
                    if bulletin:
                        url_html = link if link.startswith("http") and not link.endswith(".pdf") else None
                        url_pdf = link if link.endswith(".pdf") else None
                        
                        doc_id = f"{bulletin.lower()}_ckan_{len(documents) + 1}"
                        documents.append({
                            "id": doc_id,
                            "title": title,
                            "bulletin": bulletin,
                            "date": dt,
                            "institution": ens,
                            "category": category,
                            "urlHtml": url_html,
                            "urlPdf": url_pdf,
                            "link_to_text": get_link_to_text(url_html, url_pdf, link),
                            "is_vigent": vigent == "True" if vigent else None,
                            "source_api": "dadesobertes.seu-e.cat/api/ckan"
                        })
        except Exception as e:
            print(f"Warning: Error fetching resource {url}: {e}")
            
    return documents

def fetch_bopg_bopt_from_cido_map(cido_map_path):
    """
    Extracts all BOPG and BOPT documents (normative & non-normative) from cido_to_dogc_map.json.
    """
    documents = []
    if not os.path.exists(cido_map_path):
        return documents
        
    print(f"Extracting BOPG and BOPT documents from {cido_map_path}...")
    with open(cido_map_path, "r", encoding="utf-8") as f:
        cido_map = json.load(f)
        
    for item in cido_map:
        cido_id = str(item.get("cidoId"))
        c_title = item.get("title")
        c_type = item.get("type")
        c_inst = item.get("institucio")
        c_date = item.get("date")
        c_url = item.get("urlCido")
        
        docs = item.get("documents") or []
        for idx, d in enumerate(docs):
            bulletin = d.get("butlleti")
            if bulletin in ["BOPG", "BOPT"]:
                doc_title = d.get("descripcio") or c_title or "Document"
                doc_date = d.get("dataPublicacio") or c_date
                url_html = d.get("urlHtml")
                url_pdf = d.get("urlPdf")
                
                dogc_id = str(d.get("dogcDocumentId")) if d.get("dogcDocumentId") else None
                doc_id = dogc_id or f"cido_{cido_id}_doc_{idx}"
                
                documents.append({
                    "id": doc_id,
                    "cido_id": cido_id,
                    "title": doc_title,
                    "record_title": c_title,
                    "bulletin": bulletin,
                    "bulletin_number": d.get("numButlleti"),
                    "date": doc_date,
                    "institution": c_inst,
                    "category": c_type,
                    "fase": d.get("fase"),
                    "urlHtml": url_html,
                    "urlPdf": url_pdf,
                    "urlCido": c_url,
                    "link_to_text": get_link_to_text(url_html, url_pdf, c_url),
                    "is_vigent": d.get("esVigent"),
                    "appears_in_dogc": d.get("appearsInDogc", False),
                    "dogc_document_id": dogc_id,
                    "source_api": "dadesobertes.seu-e.cat/cido"
                })
                
    return documents

def main():
    start_time = time.time()
    cat_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data_dir = os.path.join(cat_root, "data")
    cido_map_path = os.path.join(data_dir, "cido_to_dogc_map.json")
    output_path = os.path.join(data_dir, "bopg_bopt_documents.json")
    
    api_session = requests.Session()
    api_session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    })
    
    print("--- Step 1: Querying dadesobertes.seu-e.cat CKAN API Resources ---")
    ckan_docs = fetch_ckan_resources(api_session)
    print(f"Retrieved {len(ckan_docs)} documents from dadesobertes.seu-e.cat CKAN API.")

    print("\n--- Step 2: Extracting BOPG & BOPT Documents from CIDO Dataset ---")
    cido_bop_docs = fetch_bopg_bopt_from_cido_map(cido_map_path)
    print(f"Retrieved {len(cido_bop_docs)} BOPG and BOPT documents from CIDO dataset.")

    # Merge & deduplicate documents by link_to_text / url
    all_documents = ckan_docs + cido_bop_docs
    seen_urls = set()
    deduped_documents = []
    
    bopg_count = 0
    bopt_count = 0
    
    for doc in all_documents:
        link = doc.get("link_to_text") or doc.get("urlPdf") or doc.get("urlHtml") or doc.get("urlCido")
        if link and link not in seen_urls:
            seen_urls.add(link)
            deduped_documents.append(doc)
            if doc.get("bulletin") == "BOPG":
                bopg_count += 1
            elif doc.get("bulletin") == "BOPT":
                bopt_count += 1
                
    print(f"\nTotal unique BOPG & BOPT documents: {len(deduped_documents)}")
    print(f"  - BOPG (Girona) Documents: {bopg_count}")
    print(f"  - BOPT (Tarragona) Documents: {bopt_count}")

    print(f"\nWriting dataset to {output_path}...")
    with open(output_path, "w", encoding="utf-8") as out:
        json.dump(deduped_documents, out, indent=2, ensure_ascii=False)

    print(f"Saved {output_path} ({os.path.getsize(output_path) / (1024*1024):.2f} MB) in {time.time() - start_time:.2f} seconds.")

if __name__ == "__main__":
    main()
