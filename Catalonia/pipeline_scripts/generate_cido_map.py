import os
import re
import sys
import json
import csv
import argparse
import requests
from concurrent.futures import ThreadPoolExecutor
from requests.adapters import HTTPAdapter
from urllib3.util import create_urllib3_context

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

api_session = requests.Session()
api_session.mount('https://', CustomSSLAdapter())
api_session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://cido.diba.cat/"
})

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
    if os.path.exists(backup_path):
        target_path = backup_path
    elif os.path.exists(main_path):
        target_path = main_path
        
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

def fetch_all_records_for_module(api_session, module, limit_limit=None, verbose=False):
    records = []
    limit = 100
    offset = 0
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
            records.extend(data)
            
            if verbose:
                print(f"  Paged offset {offset}: retrieved {len(data)} records...")
                
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
        
    # Check if they share distinctive long keywords (>= 5 chars)
    long_words1 = {w for w in t1 if len(w) >= 5}
    long_words2 = {w for w in t2 if len(w) >= 5}
    if long_words1 and long_words2:
        shared_long = long_words1.intersection(long_words2)
        # If they share at least 2 long words (e.g. 'draper', 'parcial')
        if len(shared_long) >= 2:
            return True
            
    return False

def deduplicate_documents(rel_docs, dogc_by_id):
    """
    Deduplicates related CIDO documents by grouping them under the same phase
    and clustering them based on description similarity and time-proximity.
    """
    by_phase = {}
    for doc in rel_docs:
        attrs = doc.get("attributes") or {}
        phase = attrs.get("fase") or "unknown_phase"
        if phase not in by_phase:
            by_phase[phase] = []
        by_phase[phase].append(doc)
        
    deduped = []
    for phase, group in by_phase.items():
        clusters = [] # list of lists of docs
        
        for doc in group:
            attrs = doc.get("attributes") or {}
            obs = attrs.get("observacionsFase") or attrs.get("resum") or ""
            obs_clean = normalize_title(obs)
            date = parse_date(attrs.get("dataPublicacio"))
            
            merged = False
            for cluster in clusters:
                rep = cluster[0]
                rep_attrs = rep.get("attributes") or {}
                rep_obs = rep_attrs.get("observacionsFase") or rep_attrs.get("resum") or ""
                rep_obs_clean = normalize_title(rep_obs)
                rep_date = parse_date(rep_attrs.get("dataPublicacio"))
                
                similar = descriptions_are_similar(obs_clean, rep_obs_clean, threshold=0.82)
                close_date = True
                if date and rep_date:
                    days_diff = abs((date - rep_date).days)
                    if days_diff > 90:
                        close_date = False
                        
                if similar and close_date:
                    cluster.append(doc)
                    merged = True
                    break
                    
            if not merged:
                clusters.append([doc])
                
        # Select best document version for each cluster
        for cluster in clusters:
            best_doc = None
            best_score = -1
            
            for doc in cluster:
                attrs = doc.get("attributes") or {}
                butlleti = attrs.get("butlleti")
                url_pdf = attrs.get("urlPdf")
                
                score = 0
                if url_pdf:
                    score += 1
                if butlleti == "BOPB":
                    score += 2
                elif butlleti == "DOGC":
                    score += 4
                    dogc_id = None
                    for url_key in ["urlPdf", "urlHtml"]:
                        url = attrs.get(url_key)
                        if url:
                            dogc_id = extract_doc_id_from_url(url)
                            if dogc_id:
                                break
                    if dogc_id and dogc_id in dogc_by_id:
                        score += 8
                        
                if score > best_score:
                    best_score = score
                    best_doc = doc
                    
            if best_doc:
                deduped.append(best_doc)
                
    return deduped


def main():
    parser = argparse.ArgumentParser(description="Generate a unified map from CIDO to DOGC documents")
    parser.add_argument("--limit-per-module", type=int, default=0, help="Number of records to fetch per module (default: 0, which fetches all)")
    parser.add_argument("--modules", type=str, default="normatives-locals,subvencions,contractacions,oposicions,convenis", help="Comma-separated CIDO modules to query")
    parser.add_argument("--output", type=str, default="data/cido_to_dogc_map.json", help="Path to save the generated mapping JSON")
    parser.add_argument("--csv-output", type=str, default="data/cido_to_dogc_map.csv", help="Path to save the generated mapping CSV")
    parser.add_argument("--verbose", action="store_true", help="Print verbose progression logs")
    args = parser.parse_args()

    cat_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data_dir = os.path.join(cat_root, "data")
    output_path = os.path.join(cat_root, args.output)
    csv_output_path = os.path.join(cat_root, args.csv_output)
    
    dogc_by_id, dogc_by_title = load_dogc_reference_data(data_dir, verbose=args.verbose)
    
    target_modules = [m.strip() for m in args.modules.split(",")]
    cido_mappings = []
    
    for module in target_modules:
        limit_val = args.limit_per_module if args.limit_per_module > 0 else None
        if limit_val:
            print(f"\nFetching up to {limit_val} records from module '{module}'...")
        else:
            print(f"\nFetching ALL available records from module '{module}' via pagination...")
            
        try:
            records = fetch_all_records_for_module(api_session, module, limit_val, args.verbose)
        except Exception as e:
            print(f"Failed to query module '{module}': {e}")
            continue
            
        print(f"Retrieved {len(records)} records. Resolving related documents in parallel...")
        
        rec_to_docs = {}
        with ThreadPoolExecutor(max_workers=20) as executor:
            futures = {
                executor.submit(fetch_documents_for_record, api_session, module, rec.get("id"), args.verbose): rec
                for rec in records
            }
            for future in futures:
                rec = futures[future]
                rec_to_docs[rec.get("id")] = future.result()
                
        for i, record in enumerate(records, 1):
            cido_id = record.get("id")
            attrs = record.get("attributes") or {}
            title = attrs.get("titol") or "Unknown Title"
            date_published = attrs.get("maxDataPublicacioDocument")
            
            rel_docs = rec_to_docs.get(cido_id) or []
            
            # Filter and deduplicate: keep only one version per phase, prioritizing DOGC versions
            deduped_docs = deduplicate_documents(rel_docs, dogc_by_id)
            
            mapped_docs = []
            for d in deduped_docs:
                d_attrs = d.get("attributes") or {}
                butlleti = d_attrs.get("butlleti")
                url_pdf = d_attrs.get("urlPdf")
                url_html = d_attrs.get("urlHtml")
                fase = d_attrs.get("fase")
                
                # Check DOGC overlap
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
                
                # Title-based fallback if direct ID check fails
                if not appears_in_dogc and dogc_by_title:
                    norm_title = normalize_title(title)
                    if norm_title in dogc_by_title:
                        matched_item = dogc_by_title[norm_title]
                        appears_in_dogc = True
                        dogc_id = matched_item.get("documentId")
                
                matching_record_summary = None
                if appears_in_dogc and dogc_id and dogc_id in dogc_by_id:
                    rec = dogc_by_id[dogc_id]
                    matching_record_summary = {
                        "documentId": rec.get("documentId"),
                        "title": rec.get("title"),
                        "dateDOGC": rec.get("dateDOGC"),
                        "organisme": rec.get("organisme")
                    }
                
                mapped_docs.append({
                    "fase": fase,
                    "descripcio": d_attrs.get("observacionsFase") or d_attrs.get("resum") or "",
                    "butlleti": butlleti,
                    "numButlleti": d_attrs.get("numButlleti"),
                    "dataPublicacio": d_attrs.get("dataPublicacio"),
                    "urlPdf": url_pdf,
                    "urlHtml": url_html,
                    "esVigent": d_attrs.get("esVigent"),
                    "appearsInDogc": appears_in_dogc,
                    "dogcDocumentId": dogc_id,
                    "matchingDogcRecord": matching_record_summary
                })
                
            # Unpack rich metadata attributes
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

            cido_mappings.append({
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
                "documents": mapped_docs
            })
            
    # Write mapped structure to output JSON
    try:
        with open(output_path, "w", encoding="utf-8") as out:
            json.dump(cido_mappings, out, indent=2, ensure_ascii=False)
        print(f"\nSuccessfully generated CIDO document JSON map at: {output_path}")
        print(f"Total mapped records: {len(cido_mappings)}")
    except Exception as e:
        print(f"Error saving output JSON to {output_path}: {e}", file=sys.stderr)

    # Write mapped structure to aggregated CSV
    headers = [
        "Module Type", "Total Records", "Records with 0 Docs", "Records with 1 Doc", "Records with >1 Docs",
        "Resolved DOGC Docs", "Unresolved DOGC Docs", "Other Sources Docs", "Total Docs"
    ]
    try:
        with open(csv_output_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()
            
            by_type = {}
            for r in cido_mappings:
                m_type = r.get("type") or "unknown"
                if m_type not in by_type:
                    by_type[m_type] = []
                by_type[m_type].append(r)
                
            totals = {
                "Module Type": "Total / All Modules",
                "Total Records": 0,
                "Records with 0 Docs": 0,
                "Records with 1 Doc": 0,
                "Records with >1 Docs": 0,
                "Resolved DOGC Docs": 0,
                "Unresolved DOGC Docs": 0,
                "Other Sources Docs": 0,
                "Total Docs": 0
            }
            
            for m_type in sorted(by_type.keys()):
                group = by_type[m_type]
                total_rec = len(group)
                zeros = 0
                ones = 0
                manys = 0
                resolved = 0
                unresolved = 0
                others = 0
                
                for r in group:
                    docs = r.get("documents") or []
                    num_docs = len(docs)
                    if num_docs == 0:
                        zeros += 1
                    elif num_docs == 1:
                        ones += 1
                    else:
                        manys += 1
                        
                    for d in docs:
                        butlleti = d.get("butlleti")
                        if butlleti == "DOGC":
                            if d.get("appearsInDogc") and d.get("matchingDogcRecord"):
                                resolved += 1
                            else:
                                unresolved += 1
                        else:
                            others += 1
                            
                tot_docs = resolved + unresolved + others
                row = {
                    "Module Type": m_type,
                    "Total Records": total_rec,
                    "Records with 0 Docs": zeros,
                    "Records with 1 Doc": ones,
                    "Records with >1 Docs": manys,
                    "Resolved DOGC Docs": resolved,
                    "Unresolved DOGC Docs": unresolved,
                    "Other Sources Docs": others,
                    "Total Docs": tot_docs
                }
                writer.writerow(row)
                
                totals["Total Records"] += total_rec
                totals["Records with 0 Docs"] += zeros
                totals["Records with 1 Doc"] += ones
                totals["Records with >1 Docs"] += manys
                totals["Resolved DOGC Docs"] += resolved
                totals["Unresolved DOGC Docs"] += unresolved
                totals["Other Sources Docs"] += others
                totals["Total Docs"] += tot_docs
                
            writer.writerow(totals)
        print(f"Successfully generated CIDO document CSV map at: {csv_output_path}")
    except Exception as e:
        print(f"Error saving output CSV to {csv_output_path}: {e}", file=sys.stderr)

if __name__ == "__main__":
    main()
