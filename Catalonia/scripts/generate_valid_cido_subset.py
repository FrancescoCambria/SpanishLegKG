import os
import sys
import json
import random
import io
import time
import requests
import pypdf
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

# Add Catalonia root to sys.path
script_dir = os.path.dirname(os.path.abspath(__file__))
cat_root = os.path.dirname(script_dir)
if cat_root not in sys.path:
    sys.path.append(cat_root)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

def extract_document_units(cido_mappings, min_year=2015, max_year=2026):
    """
    Flattens CIDO records into individual document units containing full parent context.
    """
    units = []
    for r in cido_mappings:
        c_id = r.get("cidoId")
        m_type = r.get("type")
        r_title = r.get("title")
        inst = r.get("institucio") or "Unknown"
        loc = r.get("location")
        detalls = r.get("detalls")
        ident = r.get("identificador")
        url_cido = r.get("urlCido")
        is_vigent = r.get("esVigent")
        r_date = r.get("date")

        docs = r.get("documents") or []
        for idx, d in enumerate(docs):
            dt = d.get("dataPublicacio") or r_date or ""
            yr = 0
            if len(dt) >= 4 and dt[:4].isdigit():
                yr = int(dt[:4])

            if yr < min_year or yr > max_year:
                continue

            b = d.get("butlleti") or "OTHER"
            in_dogc = bool(d.get("appearsInDogc") or b == "DOGC")
            dogc_id = str(d.get("dogcDocumentId")) if d.get("dogcDocumentId") else None

            pdf_url = d.get("urlPdf")
            if not pdf_url:
                continue

            unit = {
                "cidoId": c_id,
                "type": m_type,
                "identificador": ident,
                "recordTitle": r_title,
                "urlCido": url_cido,
                "institucio": inst,
                "isVigent": is_vigent,
                "recordDate": r_date,
                "location": loc,
                "detalls": detalls,
                "fase": d.get("fase"),
                "descripcio": d.get("descripcio"),
                "butlleti": b,
                "numButlleti": d.get("numButlleti"),
                "dataPublicacio": d.get("dataPublicacio"),
                "year": yr,
                "urlPdf": pdf_url,
                "urlHtml": d.get("urlHtml"),
                "appearsInDogc": in_dogc,
                "dogcDocumentId": dogc_id,
                "matchingDogcRecord": d.get("matchingDogcRecord"),
                "_doc_idx": idx
            }
            units.append(unit)
    return units

def check_pdf(unit):
    """
    Checks if unit's urlPdf is reachable and has page_count <= 2.
    Returns (unit, reachable, num_pages).
    """
    url = unit["urlPdf"]
    if not url:
        return unit, False, 0
    try:
        resp = requests.get(url, headers=HEADERS, timeout=5, stream=True)
        if resp.status_code != 200:
            return unit, False, 0
        content = resp.content
        if not content or len(content) < 100 or not content.startswith(b"%PDF"):
            return unit, False, 0
        
        reader = pypdf.PdfReader(io.BytesIO(content))
        num_pages = len(reader.pages)
        if 1 <= num_pages <= 2:
            return unit, True, num_pages
        else:
            return unit, False, num_pages
    except Exception:
        return unit, False, 0

def filter_valid_units(units, max_workers=30, max_needed_per_bucket=60):
    buckets = {
        ("normative", True): [u for u in units if u["type"] == "normatives-locals" and u["appearsInDogc"]],
        ("normative", False): [u for u in units if u["type"] == "normatives-locals" and not u["appearsInDogc"]],
        ("non_normative", True): [u for u in units if u["type"] != "normatives-locals" and u["appearsInDogc"]],
        ("non_normative", False): [u for u in units if u["type"] != "normatives-locals" and not u["appearsInDogc"]],
    }

    valid_units = []

    for bucket_key, pool in buckets.items():
        print(f"Testing pool {bucket_key}: candidate pool size = {len(pool)}...")
        random.shuffle(pool)
        
        # Test in chunks until we reach max_needed_per_bucket
        bucket_valid = []
        chunk_size = 200
        for i in range(0, min(len(pool), 2000), chunk_size):
            chunk = pool[i:i+chunk_size]
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_unit = {executor.submit(check_pdf, u): u for u in chunk}
                for future in as_completed(future_to_unit):
                    unit, reachable, num_pages = future.result()
                    if reachable and 1 <= num_pages <= 2:
                        unit["numPages"] = num_pages
                        unit["pdfReachable"] = True
                        bucket_valid.append(unit)
                        if len(bucket_valid) >= max_needed_per_bucket:
                            break
            if len(bucket_valid) >= max_needed_per_bucket:
                break

        print(f"Pool {bucket_key}: found {len(bucket_valid)} valid units (reachable, <= 2 pages).")
        valid_units.extend(bucket_valid)

    return valid_units

def sample_balanced_subset(units, total=100, norm_ratio=0.30, dogc_ratio=0.30, seed=42):
    random.seed(seed)
    
    target_norm = int(round(total * norm_ratio))
    target_non_norm = total - target_norm

    target_norm_dogc = int(round(target_norm * dogc_ratio))
    target_norm_nondogc = target_norm - target_norm_dogc

    target_nonnorm_dogc = int(round(target_non_norm * dogc_ratio))
    target_nonnorm_nondogc = target_non_norm - target_nonnorm_dogc

    buckets = {
        ("normative", True): [u for u in units if u["type"] == "normatives-locals" and u["appearsInDogc"]],
        ("normative", False): [u for u in units if u["type"] == "normatives-locals" and not u["appearsInDogc"]],
        ("non_normative", True): [u for u in units if u["type"] != "normatives-locals" and u["appearsInDogc"]],
        ("non_normative", False): [u for u in units if u["type"] != "normatives-locals" and not u["appearsInDogc"]],
    }

    quotas = {
        ("normative", True): target_norm_dogc,
        ("normative", False): target_norm_nondogc,
        ("non_normative", True): target_nonnorm_dogc,
        ("non_normative", False): target_nonnorm_nondogc,
    }

    selected = []

    def sample_from_pool(pool, count, max_per_inst=2):
        if not pool or count <= 0:
            return []
        
        by_year_gaz = defaultdict(list)
        for u in pool:
            by_year_gaz[(u["year"], u["butlleti"])].append(u)
            
        keys = list(by_year_gaz.keys())
        random.shuffle(keys)
        
        picked = []
        inst_counts = Counter()

        for _ in range(count * 5):
            if len(picked) >= count:
                break
            for key in keys:
                if len(picked) >= count:
                    break
                candidates = [c for c in by_year_gaz[key] if inst_counts[c["institucio"]] < max_per_inst and c not in picked]
                if candidates:
                    chosen = random.choice(candidates)
                    picked.append(chosen)
                    inst_counts[chosen["institucio"]] += 1

        if len(picked) < count:
            remaining = [u for u in pool if u not in picked]
            random.shuffle(remaining)
            picked.extend(remaining[:(count - len(picked))])
            
        return picked

    for key, q_count in quotas.items():
        pool = buckets[key]
        sampled = sample_from_pool(pool, q_count)
        selected.extend(sampled)

    random.shuffle(selected)
    return selected

def enrich_subset_with_reference_ids(subset, cat_root):
    hier_dir = os.path.join(cat_root, "data/hierarchical_output")
    cido_nodes_path = os.path.join(hier_dir, "cido_nodes.json")
    sec_rels_path = os.path.join(cat_root, "data/prepared_graph_data/has_section_relationships.json")

    doc_to_sections = {}
    if os.path.exists(sec_rels_path):
        with open(sec_rels_path, "r", encoding="utf-8") as f:
            sec_rels = json.load(f)
        for rel in sec_rels:
            doc_id = str(rel.get("documentId"))
            sec_id = rel.get("sectionId")
            if doc_id and sec_id:
                if doc_id not in doc_to_sections:
                    doc_to_sections[doc_id] = []
                doc_to_sections[doc_id].append(sec_id)

    cido_nodes_map = {}
    if os.path.exists(cido_nodes_path):
        with open(cido_nodes_path, "r", encoding="utf-8") as f:
            cido_nodes_list = json.load(f)
        cido_nodes_map = {str(c.get("id")): c for c in cido_nodes_list}

    for item in subset:
        cid = str(item.get("cidoId")) if item.get("cidoId") else None
        did = str(item.get("dogcDocumentId")) if item.get("dogcDocumentId") else None
        idx = item.pop("_doc_idx", 0)

        item["cidoNodeId"] = cid
        c_node = cido_nodes_map.get(cid)
        if c_node:
            item["documentIds"] = c_node.get("document_ids", [])
        else:
            item["documentIds"] = [did] if did else []

        if did:
            item["documentNodeId"] = did
        elif cid:
            synthetic_id = f"cido_{cid}_doc_{idx}"
            item["documentNodeId"] = synthetic_id
        else:
            item["documentNodeId"] = None

        target_doc_id = did or item.get("documentNodeId")
        if target_doc_id and target_doc_id in doc_to_sections:
            item["sectionIds"] = doc_to_sections[target_doc_id]
        else:
            item["sectionIds"] = []

    return subset

def main():
    input_path = os.path.join(cat_root, "data/cido_to_dogc_map.json")
    output_path = os.path.join(cat_root, "data/cido_subset_100.json")

    print(f"Loading CIDO mappings from {input_path}...")
    with open(input_path, "r", encoding="utf-8") as f:
        cido_mappings = json.load(f)

    print("Extracting document candidates (2015-2026)...")
    units = extract_document_units(cido_mappings, 2015, 2026)
    print(f"Extracted {len(units)} raw document candidates.")

    print("Filtering candidates for HTTP reachability and page count <= 2...")
    valid_units = filter_valid_units(units, max_workers=30, max_needed_per_bucket=60)
    print(f"Total valid candidate pool: {len(valid_units)}")

    print("Sampling balanced subset of 100 documents...")
    subset = sample_balanced_subset(valid_units, total=100, norm_ratio=0.30, dogc_ratio=0.30, seed=42)

    print("Enriching subset with graph reference IDs...")
    subset = enrich_subset_with_reference_ids(subset, cat_root)

    print(f"Saving subset JSON to {output_path}...")
    with open(output_path, "w", encoding="utf-8") as out:
        json.dump(subset, out, indent=2, ensure_ascii=False)

    hier_subset_path = os.path.join(cat_root, "data/hierarchical_output/hierarchical_cido_subset_100.json")
    if os.path.exists(os.path.dirname(hier_subset_path)):
        with open(hier_subset_path, "w", encoding="utf-8") as out:
            json.dump(subset, out, indent=2, ensure_ascii=False)

    print("\n--- Subset Recreation Summary ---")
    print(f"Total subset records: {len(subset)}")
    print(f"Page counts distribution: {Counter(s['numPages'] for s in subset)}")
    print(f"Reachable PDFs count: {sum(1 for s in subset if s.get('pdfReachable'))}/100")
    print(f"Normative count: {sum(1 for s in subset if s['type'] == 'normatives-locals')}/100")
    print(f"DOGC count: {sum(1 for s in subset if s['appearsInDogc'])}/100")
    print(f"Year range: {min(s['year'] for s in subset)} - {max(s['year'] for s in subset)}")
    print("Done!")

if __name__ == "__main__":
    main()
