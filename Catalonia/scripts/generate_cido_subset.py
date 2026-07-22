import os
import sys
import json
import random
import argparse
from collections import Counter, defaultdict

# Add Catalonia root to sys.path
script_dir = os.path.dirname(os.path.abspath(__file__))
cat_root = os.path.dirname(script_dir)
if cat_root not in sys.path:
    sys.path.append(cat_root)

def extract_document_units(cido_mappings, min_year, max_year):
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
                "urlPdf": d.get("urlPdf"),
                "urlHtml": d.get("urlHtml"),
                "appearsInDogc": in_dogc,
                "dogcDocumentId": dogc_id,
                "matchingDogcRecord": d.get("matchingDogcRecord"),
                "_doc_idx": idx
            }
            units.append(unit)
    return units

def sample_balanced_subset(units, total=100, norm_ratio=0.30, dogc_ratio=0.30, min_year=2015, max_year=2026, seed=42):
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
    """
    Enriches each item in the subset with cidoNodeId, documentNodeId, documentIds, and sectionIds.
    """
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
    parser = argparse.ArgumentParser(description="Generate a balanced 100-document subset from CIDO mappings with reference IDs")
    parser.add_argument("--input", type=str, default="data/cido_to_dogc_map.json", help="Path to cido_to_dogc_map.json")
    parser.add_argument("--output", type=str, default="data/cido_subset_100.json", help="Path to save the output subset JSON")
    parser.add_argument("--total", type=int, default=100, help="Total number of documents in subset (default: 100)")
    parser.add_argument("--normative-ratio", type=float, default=0.30, help="Target ratio of normative documents (default: 0.30)")
    parser.add_argument("--dogc-ratio", type=float, default=0.30, help="Target ratio of DOGC documents (default: 0.30)")
    parser.add_argument("--min-year", type=int, default=2015, help="Minimum publication year (default: 2015)")
    parser.add_argument("--max-year", type=int, default=2026, help="Maximum publication year (default: 2026)")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for reproducible sampling (default: 42)")
    args = parser.parse_args()

    input_path = os.path.join(cat_root, args.input)
    output_path = os.path.join(cat_root, args.output)

    if not os.path.exists(input_path):
        print(f"Error: Input JSON file {input_path} not found.")
        sys.exit(1)

    print(f"Loading CIDO mappings from {input_path}...")
    with open(input_path, "r", encoding="utf-8") as f:
        cido_mappings = json.load(f)

    print(f"Extracting candidate document units ({args.min_year}-{args.max_year})...")
    units = extract_document_units(cido_mappings, args.min_year, args.max_year)
    print(f"Retrieved {len(units)} document candidates.")

    print(f"Sampling balanced subset of {args.total} documents...")
    subset = sample_balanced_subset(
        units,
        total=args.total,
        norm_ratio=args.normative_ratio,
        dogc_ratio=args.dogc_ratio,
        min_year=args.min_year,
        max_year=args.max_year,
        seed=args.seed
    )

    print(f"Enriching subset with reference IDs (cidoNodeId, documentNodeId, documentIds, sectionIds)...")
    subset = enrich_subset_with_reference_ids(subset, cat_root)

    print(f"Saving subset JSON to {output_path}...")
    with open(output_path, "w", encoding="utf-8") as out:
        json.dump(subset, out, indent=2, ensure_ascii=False)

    hier_subset_path = os.path.join(cat_root, "data/hierarchical_output/hierarchical_cido_subset_100.json")
    if os.path.exists(os.path.dirname(hier_subset_path)):
        with open(hier_subset_path, "w", encoding="utf-8") as out:
            json.dump(subset, out, indent=2, ensure_ascii=False)

    print("\n--- Subset Composition Summary ---")
    print(f"Total documents: {len(subset)}")
    print(f"Enriched records: {len(subset)} entries containing cidoNodeId, documentNodeId, documentIds, sectionIds.")
    print(f"Subset successfully saved to: {output_path}")

if __name__ == "__main__":
    main()
