import os
import sys
import json
import time
import requests

def get_link_to_text(html_url, pdf_url, fallback=None):
    for val in [html_url, pdf_url, fallback]:
        if isinstance(val, dict) and val.get("url"):
            val = val.get("url")
        if val and isinstance(val, str) and val.strip():
            return val.strip()
    return None

def fetch_socrata_datasets(api_session):
    """
    Fetches documents from Socrata Open Data API: https://[domain]/resource/[dataset_identifier].json
    Domain: analisi.transparenciacatalunya.cat
    """
    domain = "analisi.transparenciacatalunya.cat"
    endpoints = [
        ("ybgg-dgi6", "nom_organ", "Contractació - Publicacions PSCP"),
        ("hb6v-jcbf", "organisme_contractant", "Contractació - Registre"),
        ("s9xt-n979", "entitat_oo_aa_o_departament", "Subvencions - RAISC"),
        ("exh2-diuf", "organismes_signants_per_part", "Convenis"),
        ("n6hn-rmy7", None, "Normativa")
    ]
    
    documents = []
    
    for dataset_id, organ_col, cat_name in endpoints:
        url = f"https://{domain}/resource/{dataset_id}.json"
        
        params = {"$limit": 50000}
        if organ_col:
            params["$where"] = f"{organ_col} like '%Diputació de Lleida%' or {organ_col} like '%Diputacio de Lleida%' or {organ_col} like '%Lleida%'"
            
        try:
            r = api_session.get(url, params=params, timeout=20)
            if r.status_code == 200:
                items = r.json()
                print(f"Retrieved {len(items)} items from Socrata dataset '{dataset_id}' ({cat_name}).")
                
                for idx, item in enumerate(items):
                    doc_id = item.get("id_intern") or item.get("codi_expedient") or item.get("clau") or f"socrata_{dataset_id}_{idx+1}"
                    
                    title = (item.get("denominacio") or item.get("descripcio_expedient") or 
                             item.get("t_tol_convocat_ria_catal") or item.get("t_tol_conveni") or 
                             item.get("t_tol_de_la_norma") or item.get("objecte_contracte") or "Document")
                             
                    date_val = (item.get("data_publicacio_contracte") or item.get("data_adjudicacio") or 
                                item.get("data_concessi") or item.get("data_signatura") or 
                                item.get("data_del_document") or item.get("any"))
                                
                    if isinstance(date_val, str) and "T" in date_val:
                        date_val = date_val.split("T")[0]
                        
                    enllac = item.get("enllac_publicacio")
                    url_html = None
                    if isinstance(enllac, dict):
                        url_html = enllac.get("url")
                    elif isinstance(enllac, str):
                        url_html = enllac
                        
                    url_pdf = item.get("format_pdf") or item.get("bases_reguladores_url_catal")
                    if isinstance(url_pdf, dict):
                        url_pdf = url_pdf.get("url")
                        
                    doc_link = get_link_to_text(url_html, url_pdf)
                    
                    inst = item.get("nom_organ") or item.get("organisme_contractant") or "Diputació de Lleida"
                    
                    documents.append({
                        "id": str(doc_id),
                        "title": title,
                        "bulletin": "BOPL",
                        "date": date_val,
                        "institution": inst,
                        "category": cat_name,
                        "urlHtml": url_html,
                        "urlPdf": url_pdf,
                        "link_to_text": doc_link,
                        "socrata_dataset_id": dataset_id,
                        "source_api": f"https://{domain}/resource/{dataset_id}.json"
                    })
            else:
                print(f"Warning: Socrata API {dataset_id} returned HTTP {r.status_code}")
        except Exception as e:
            print(f"Warning: Failed to fetch Socrata dataset {dataset_id}: {e}")
            
    return documents

def fetch_bopl_from_cido_map(cido_map_path):
    """
    Extracts all BOPL (Lleida) documents from cido_to_dogc_map.json.
    """
    documents = []
    if not os.path.exists(cido_map_path):
        return documents
        
    print(f"Extracting BOPL (Lleida) documents from {cido_map_path}...")
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
            if bulletin == "BOPL":
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
                    "bulletin": "BOPL",
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
    output_path = os.path.join(data_dir, "bopl_documents.json")
    
    api_session = requests.Session()
    api_session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    })
    
    print("--- Step 1: Querying Socrata Open Data API (https://[domain]/resource/[dataset_identifier].json) ---")
    socrata_docs = fetch_socrata_datasets(api_session)
    print(f"Retrieved {len(socrata_docs)} documents from Socrata Open Data API.")

    print("\n--- Step 2: Extracting BOPL Documents from CIDO Dataset ---")
    cido_bopl_docs = fetch_bopl_from_cido_map(cido_map_path)
    print(f"Retrieved {len(cido_bopl_docs)} BOPL documents from CIDO dataset.")

    # Merge & deduplicate by link_to_text / url / title+date
    all_documents = socrata_docs + cido_bopl_docs
    seen_keys = set()
    deduped_documents = []
    
    for doc in all_documents:
        link = doc.get("link_to_text") or doc.get("urlPdf") or doc.get("urlHtml") or doc.get("urlCido") or doc.get("id")
        if isinstance(link, dict):
            link = link.get("url") or str(link)
        if link and link not in seen_keys:
            seen_keys.add(link)
            deduped_documents.append(doc)

    print(f"\nTotal unique BOPL (Lleida) documents: {len(deduped_documents)}")

    print(f"Writing dataset to {output_path}...")
    with open(output_path, "w", encoding="utf-8") as out:
        json.dump(deduped_documents, out, indent=2, ensure_ascii=False)

    print(f"Saved {output_path} ({os.path.getsize(output_path) / (1024*1024):.2f} MB) in {time.time() - start_time:.2f} seconds.")

if __name__ == "__main__":
    main()
