#!/usr/bin/env python3
"""
generate_cido_map.py

Fetches CIDO database records across modules (normatives-locals, subvencions, contractacions, oposicions, convenis),
resolves related document links and materies (descriptors), matches DOGC documents, and outputs cido_to_dogc_map.json.

Supports incremental update mode (--incremental) to only fetch newly published records since last run.
"""

import os
import re
import sys
import json
import csv
import argparse
import requests
from concurrent.futures import ThreadPoolExecutor
from requests.adapters import HTTPAdapter
try:
    from urllib3.util import create_urllib3_context
except ImportError:
    from urllib3.util.ssl_ import create_urllib3_context

# Add Catalonia root directory to sys.path
script_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(script_dir)
for d in [script_dir, parent_dir]:
    if d not in sys.path:
        sys.path.append(d)

try:
    from html_parser import extract_doc_id_from_url
except ImportError as e:
    print(f"Error importing html_parser utilities: {e}")
    sys.exit(1)

# Custom SSL adapter to handle legacy connections safely
class CustomSSLAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        context = create_urllib3_context()
        context.set_ciphers('DEFAULT@SECLEVEL=1')
        kwargs['ssl_context'] = context
        return super(CustomSSLAdapter, self).init_poolmanager(*args, **kwargs)

def setup_api_session():
    session = requests.Session()
    session.mount('https://', CustomSSLAdapter())
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://cido.diba.cat/"
    })
    return session

def normalize_title(title):
    if not title:
        return ""
    t = title.lower()
    t = re.sub(r'[^\w\s]', ' ', t)
    t = re.sub(r'\s+', ' ', t).strip()
    
    prefixes = ['ordre', 'orden', 'resolució', 'resolución', 'anunci', 'anuncio', 'edicte', 'edicto', 'decret', 'decreto', 'llei', 'ley']
    for p in prefixes:
        if t.startswith(p):
            t = t[len(p):].strip()
    return t

def load_dogc_reference_data(data_dir, verbose=False):
    dogc_by_id = {}
    dogc_by_title = {}
    
    backup_path = os.path.join(data_dir, "dogc_documents_2024_2026_backup.json")
    main_path = os.path.join(data_dir, "dogc_documents.json")
    
    target_path = None
    if os.path.exists(main_path):
        target_path = main_path
    elif os.path.exists(backup_path):
        target_path = backup_path
        
    if not target_path:
        if verbose:
            print("Warning: No DOGC reference JSON found. Direct ID check will be used.")
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
    except Exception as e:
        print(f"Error loading DOGC reference data: {e}", file=sys.stderr)
        
    return dogc_by_id, dogc_by_title

def fetch_documents_for_record(api_session, module, cido_id, verbose=False):
    rel_docs_url = f"https://api.diba.cat/dadesobertes/cido/v1/{module}/{cido_id}/documents"
    try:
        r_docs = api_session.get(rel_docs_url, timeout=10)
        if r_docs.status_code == 200:
            return r_docs.json().get("data") or []
    except Exception as e:
        if verbose:
            print(f"  Warning: Failed to fetch documents for record {cido_id}: {e}")
    return []

def fetch_materies_for_record(api_session, module, cido_id, verbose=False):
    rel_materies_url = f"https://api.diba.cat/dadesobertes/cido/v1/{module}/{cido_id}/materies"
    try:
        r_mat = api_session.get(rel_materies_url, timeout=10)
        if r_mat.status_code == 200:
            raw_data = r_mat.json().get("data") or []
            materies = []
            for item in raw_data:
                m_id = str(item.get("id") or "")
                attrs = item.get("attributes") or {}
                m_name = attrs.get("materia") or ""
                if m_name:
                    materies.append({"id": m_id, "name": m_name})
            return materies
    except Exception as e:
        if verbose:
            print(f"  Warning: Failed to fetch materies for record {cido_id}: {e}")
    return []

def fetch_record_details(api_session, module, cido_id, verbose=False):
    docs = fetch_documents_for_record(api_session, module, cido_id, verbose=verbose)
    mats = fetch_materies_for_record(api_session, module, cido_id, verbose=verbose)
    return docs, mats

def fetch_all_records_for_module(api_session, module, existing_ids=None, limit_limit=None, verbose=False):
    records = []
    limit = 100
    offset = 0
    stop_early = False
    
    while True:
        url = f"https://api.diba.cat/dadesobertes/cido/v1/{module}?sort=-maxDataPublicacioDocument&page[limit]={limit}&page[offset]={offset}"
        try:
            r = api_session.get(url, timeout=15)
            if r.status_code != 200:
                print(f"  Error querying {module} at offset {offset}: HTTP {r.status_code}")
                break
            data = r.json().get("data") or []
            if not data:
                break
                
            new_batch = []
            for item in data:
                c_id = str(item.get("id") or "")
                if existing_ids and c_id in existing_ids:
                    stop_early = True
                    break
                new_batch.append(item)
                
            records.extend(new_batch)
            
            if verbose:
                print(f"  Paged offset {offset}: retrieved {len(new_batch)} new records...")
                
            if stop_early:
                if verbose:
                    print(f"  Reached already-scraped record in {module}. Stopping incremental pagination.")
                break
                
            if limit_limit and len(records) >= limit_limit:
                records = records[:limit_limit]
                break
            if len(data) < limit:
                break
            offset += limit
        except Exception as e:
            print(f"  Exception during pagination of {module} at offset {offset}: {e}")
            break
    return records

def parse_date(date_str):
    if not date_str or date_str == "unknown_date":
        return None
    try:
        from datetime import datetime
        return datetime.strptime(date_str, "%Y-%m-%d")
    except Exception:
        return None

def descriptions_are_similar(desc1, desc2, threshold=0.82):
    if len(desc1) >= 15 and len(desc2) >= 15:
        if desc1 in desc2 or desc2 in desc1:
            return True
            
    t1 = set(desc1.split())
    t2 = set(desc2.split())
    if not t1 or not t2:
        return False
    intersection = t1.intersection(t2)
    ratio = len(intersection) / max(len(t1), len(t2))
    if ratio >= threshold:
        return True
        
    long_words1 = {w for w in t1 if len(w) >= 5}
    long_words2 = {w for w in t2 if len(w) >= 5}
    if long_words1 and long_words2:
        shared_long = long_words1.intersection(long_words2)
        if len(shared_long) >= 2:
            return True
            
    return False

def get_source_priority(doc, dogc_by_id):
    attrs = doc.get("attributes") or {}
    butlleti = (attrs.get("butlleti") or "").upper()
    url = attrs.get("urlPdf") or attrs.get("urlHtml") or ""
    c_id = extract_doc_id_from_url(url)
    
    if butlleti == "DOGC":
        if c_id and str(c_id) in dogc_by_id:
            return 4  # DOGC with matched documentId in DOGC database
        return 3      # DOGC gazette
    elif butlleti.startswith("BOP"):
        return 2      # Provincial Bulletins (BOPB, BOPG, BOPL, BOPT)
    else:
        return 1      # Other (BOE, TA, GASETA, etc.)

def check_url_active(api_session, url, timeout=5):
    if not url:
        return False, None
    try:
        r = api_session.head(url, allow_redirects=True, timeout=timeout)
        if r.status_code in [200, 301, 302, 303, 307, 308]:
            return True, r.status_code
        r_get = api_session.get(url, allow_redirects=True, stream=True, timeout=timeout)
        r_get.close()
        if r_get.status_code == 200:
            return True, 200
        return False, r_get.status_code
    except Exception:
        return False, None

def deduplicate_documents(rel_docs, dogc_by_id):
    by_phase = {}
    for doc in rel_docs:
        attrs = doc.get("attributes") or {}
        phase = attrs.get("fase") or "unknown_phase"
        if phase not in by_phase:
            by_phase[phase] = []
        by_phase[phase].append(doc)
        
    deduped = []
    for phase, group in by_phase.items():
        clusters = []
        for doc in group:
            attrs = doc.get("attributes") or {}
            obs = attrs.get("observacionsFase") or attrs.get("resum") or ""
            obs_clean = normalize_title(obs)
            
            merged = False
            for cluster in clusters:
                rep = cluster[0]
                rep_attrs = rep.get("attributes") or {}
                rep_obs = rep_attrs.get("observacionsFase") or rep_attrs.get("resum") or ""
                rep_obs_clean = normalize_title(rep_obs)
                
                if descriptions_are_similar(obs_clean, rep_obs_clean, threshold=0.82):
                    cluster.append(doc)
                    merged = True
                    break
            if not merged:
                clusters.append([doc])
                
        for cluster in clusters:
            # Track all publishing sources across cluster items
            all_sources = set()
            for doc in cluster:
                b = (doc.get("attributes") or {}).get("butlleti")
                if b:
                    all_sources.add(b)
            all_sources_list = sorted(list(all_sources))
            
            # Select best document based on priority hierarchy: DOGC > BOP > Other
            best_doc = cluster[0]
            best_prio = get_source_priority(best_doc, dogc_by_id)
            for candidate in cluster[1:]:
                cand_prio = get_source_priority(candidate, dogc_by_id)
                if cand_prio > best_prio:
                    best_doc = candidate
                    best_prio = cand_prio
                    
            # Attach source tracking properties
            best_doc["_all_sources"] = all_sources_list
            best_doc["_is_multi_source"] = len(all_sources_list) > 1
            deduped.append(best_doc)
            
    return deduped

def main():
    parser = argparse.ArgumentParser(description="Generate CIDO document JSON map with DOGC document matching and materies")
    parser.add_argument("--data-dir", type=str, default="data", help="Directory containing data files")
    parser.add_argument("--output", type=str, default=None, help="Path for output JSON map")
    parser.add_argument("--csv-output", type=str, default=None, help="Path for summary CSV map")
    parser.add_argument("--limit-per-module", type=int, default=None, help="Limit number of records per CIDO module")
    parser.add_argument("--workers", type=int, default=20, help="Number of concurrent thread workers for detail fetching")
    parser.add_argument("--incremental", action="store_true", help="Incremental mode: stop pagination when encountering already-scraped records")
    parser.add_argument("--check-urls", action="store_true", help="Verify live reachability of publication URLs and flag eliminated ones")
    parser.add_argument("--skip-resolve", action="store_true", help="Skip automatic execution of resolve_unresolved_dogc post-processing")
    args = parser.parse_args()

    data_dir = os.path.abspath(args.data_dir if os.path.isabs(args.data_dir) else os.path.join(parent_dir, args.data_dir))
    default_json = os.path.join(data_dir, "cido_documents.json")
    if not os.path.exists(default_json) and os.path.exists(os.path.join(data_dir, "cido_to_dogc_map.json")):
        default_json = os.path.join(data_dir, "cido_to_dogc_map.json")
    output_path = os.path.abspath(args.output if args.output else default_json)
    csv_output_path = os.path.abspath(args.csv_output if args.csv_output else os.path.join(data_dir, "cido_documents.csv"))

    api_session = setup_api_session()
    dogc_by_id, dogc_by_title = load_dogc_reference_data(data_dir, verbose=True)

    # Load existing CIDO mappings if incremental
    existing_mappings = []
    existing_ids = set()
    if args.incremental and os.path.exists(output_path):
        print(f"Loading existing CIDO mappings from {output_path} for incremental update...")
        try:
            with open(output_path, "r", encoding="utf-8") as f:
                existing_mappings = json.load(f)
            for r in existing_mappings:
                cid = r.get("cidoId")
                if cid:
                    existing_ids.add(str(cid))
            print(f"Loaded {len(existing_mappings)} existing CIDO records.")
        except Exception as e:
            print(f"Warning loading existing CIDO map: {e}")

    modules = ["normatives-locals", "subvencions", "contractacions", "oposicions", "convenis"]
    new_cido_mappings = []

    for module in modules:
        print(f"\nFetching records from module '{module}' via pagination...")
        records = fetch_all_records_for_module(api_session, module, existing_ids=existing_ids if args.incremental else None, limit_limit=args.limit_per_module, verbose=True)
        print(f"Retrieved {len(records)} new records for module '{module}'. Resolving related details in parallel...")
        
        if not records:
            continue
            
        def process_record(rec):
            c_id = rec.get("id")
            docs, mats = fetch_record_details(api_session, module, c_id)
            return c_id, docs, mats
            
        rec_to_details = {}
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = [executor.submit(process_record, r) for r in records]
            for fut in futures:
                try:
                    c_id, docs, mats = fut.result()
                    rec_to_details[c_id] = (docs, mats)
                except Exception as e:
                    pass
                    
        for rec in records:
            cido_id = str(rec.get("id"))
            attrs = rec.get("attributes") or {}
            title = attrs.get("titol") or "Untitled Record"
            date_published = attrs.get("maxDataPublicacioDocument")
            
            rel_docs, materies = rec_to_details.get(cido_id) or ([], [])
            deduped_docs = deduplicate_documents(rel_docs, dogc_by_id)
            
            mapped_docs = []
            for d in deduped_docs:
                d_attrs = d.get("attributes") or {}
                butlleti = d_attrs.get("butlleti")
                url_pdf = d_attrs.get("urlPdf")
                url_html = d_attrs.get("urlHtml")
                fase = d_attrs.get("fase")
                
                appears_in_dogc = False
                dogc_id = None
                
                if butlleti == "DOGC":
                    for url_key in ["urlPdf", "urlHtml"]:
                        url = d_attrs.get(url_key)
                        if url:
                            dogc_id = extract_doc_id_from_url(url)
                            if dogc_id:
                                break
                    if dogc_id:
                        appears_in_dogc = True
                
                if not appears_in_dogc and dogc_by_title:
                    norm_title = normalize_title(title)
                    if norm_title in dogc_by_title:
                        matched_item = dogc_by_title[norm_title]
                        appears_in_dogc = True
                        dogc_id = matched_item.get("documentId")
                
                matching_record_summary = None
                if appears_in_dogc and dogc_id and dogc_id in dogc_by_id:
                    rec_dogc = dogc_by_id[dogc_id]
                    matching_record_summary = {
                        "documentId": rec_dogc.get("documentId"),
                        "title": rec_dogc.get("title"),
                        "dateDOGC": rec_dogc.get("dateDOGC"),
                        "organisme": rec_dogc.get("organisme")
                    }
                
                # Active URL reachability check
                target_url = url_pdf or url_html
                is_url_active, status_code = check_url_active(api_session, target_url) if args.check_urls else (True, 200)

                mapped_docs.append({
                    "fase": fase,
                    "descripcio": d_attrs.get("observacionsFase") or d_attrs.get("resum") or "",
                    "butlleti": butlleti,
                    "numButlleti": d_attrs.get("numButlleti"),
                    "dataPublicacio": d_attrs.get("dataPublicacio"),
                    "urlPdf": url_pdf,
                    "urlHtml": url_html,
                    "isUrlActive": is_url_active,
                    "urlStatus": "active" if is_url_active else "eliminated",
                    "urlStatusCode": status_code,
                    "allSources": d.get("_all_sources") or ([butlleti] if butlleti else []),
                    "isMultiSource": d.get("_is_multi_source", False),
                    "esVigent": d_attrs.get("esVigent"),
                    "appearsInDogc": appears_in_dogc,
                    "dogcDocumentId": dogc_id,
                    "matchingDogcRecord": matching_record_summary
                })
                
            institucio = attrs.get("institucioDesenvolupat") or attrs.get("ambit") or "Unknown"
            es_vigent = attrs.get("esVigent")
            identificador = attrs.get("identificador")
            
            location = None
            if attrs.get("latitud") and attrs.get("longitud"):
                location = {
                    "lat": attrs.get("latitud"),
                    "lon": attrs.get("longitud")
                }
                
            detalls = {}
            if module == "convenis":
                detalls["codi"] = attrs.get("codi")
                detalls["ambit"] = attrs.get("ambit")
            elif module == "oposicions":
                detalls["numPlaces"] = attrs.get("numPlaces")
                detalls["grupTitulacio"] = attrs.get("grupTitulacio")
                detalls["sistemaSeleccio"] = attrs.get("sistemaSeleccio")
                detalls["borsaTreball"] = attrs.get("borsaTreball")
            elif module == "contractacions":
                detalls["tipusContracte"] = attrs.get("tipusContracte")
                detalls["procediment"] = attrs.get("procediment")
                detalls["importIvaExclos"] = attrs.get("importIvaExclos")
                detalls["expedient"] = attrs.get("expedient")
            elif module == "subvencions":
                detalls["tipusSubvencio"] = attrs.get("tipusSubvencio")
                detalls["expedient"] = attrs.get("expedient")
                detalls["dataFinalitzacio"] = attrs.get("dataFinalitzacio")

            new_cido_mappings.append({
                "cidoId": cido_id,
                "type": module,
                "identificador": identificador,
                "title": title,
                "urlCido": attrs.get("urlCido"),
                "institucio": institucio,
                "esVigent": es_vigent,
                "date": date_published,
                "location": location,
                "detalls": detalls,
                "materies": materies,
                "documents": mapped_docs
            })

    final_cido_mappings = new_cido_mappings + existing_mappings
    print(f"\nTotal CIDO mappings (new: {len(new_cido_mappings)}, total: {len(final_cido_mappings)}). Saving to {output_path}...")
    
    try:
        with open(output_path, "w", encoding="utf-8") as out:
            json.dump(final_cido_mappings, out, indent=2, ensure_ascii=False)
        print(f"Successfully generated CIDO document JSON map at: {output_path}")

        if not getattr(args, 'skip_resolve', False):
            print("\n[Post-Processing] Running automatic resolution of unresolved DOGC entries (resolve_unresolved_dogc.py)...")
            resolve_script = os.path.join(script_dir, "resolve_unresolved_dogc.py")
            if os.path.exists(resolve_script):
                subprocess.run([sys.executable, resolve_script, "--input", output_path], check=False)
    except Exception as e:
        print(f"Error saving output JSON to {output_path}: {e}", file=sys.stderr)

if __name__ == "__main__":
    main()
