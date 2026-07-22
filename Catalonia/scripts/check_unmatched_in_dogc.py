import os
import sys
import json
import re
import time
from collections import defaultdict

def normalize_text(text):
    if not text:
        return ""
    t = text.lower()
    t = re.sub(r'[^\w\s]', ' ', t)
    t = re.sub(r'\s+', ' ', t).strip()
    return t

def parse_date(dt_str):
    if not dt_str or not isinstance(dt_str, str):
        return None
    dt_str = dt_str.strip()
    if "T" in dt_str:
        dt_str = dt_str.split("T")[0]
    # DD/MM/YYYY -> YYYY-MM-DD
    if "/" in dt_str:
        parts = dt_str.split("/")
        if len(parts) == 3 and len(parts[2]) == 4:
            return f"{parts[2]}-{parts[1].zfill(2)}-{parts[0].zfill(2)}"
    return dt_str

def main():
    start_time = time.time()
    cat_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data_dir = os.path.join(cat_root, "data")
    
    bopl_path = os.path.join(data_dir, "bopl_documents.json")
    dogc_path = os.path.join(data_dir, "dogc_documents.json")
    output_report = os.path.join(data_dir, "unmatched_dogc_matching_report.json")
    
    if not os.path.exists(bopl_path) or not os.path.exists(dogc_path):
        print("Error: Missing input data files.")
        sys.exit(1)
        
    print(f"Loading BOPL document records from {bopl_path}...")
    with open(bopl_path, "r", encoding="utf-8") as f:
        bopl_docs = json.load(f)
    print(f"Loaded {len(bopl_docs)} total BOPL/Socrata records.")

    print("Loading DOGC dataset (dogc_documents.json)...")
    with open(dogc_path, "r", encoding="utf-8") as f:
        dogc_docs = json.load(f)
    print(f"Loaded {len(dogc_docs)} official DOGC document records.")

    # Index DOGC dataset
    print("Indexing DOGC dataset lookups...")
    dogc_by_id = {}
    dogc_by_url = {}
    dogc_by_title_date = {}
    dogc_by_title_only = {}
    
    for item in dogc_docs:
        did = str(item.get("documentId") or item.get("id") or "")
        if did:
            dogc_by_id[did] = item
            
        u_html = item.get("htmlUrl") or item.get("urlHtml")
        u_pdf = item.get("pdfUrl") or item.get("urlPdf")
        
        if u_html:
            dogc_by_url[u_html.strip()] = did
        if u_pdf:
            dogc_by_url[u_pdf.strip()] = did
            
        t_norm = normalize_text(item.get("title"))
        dt_norm = parse_date(item.get("dateDOGC") or item.get("date"))
        
        if t_norm:
            if dt_norm:
                dogc_by_title_date[(t_norm, dt_norm)] = did
            if len(t_norm) > 15:
                dogc_by_title_only[t_norm] = did

    print(f"Indexed {len(dogc_by_url)} DOGC URLs, {len(dogc_by_title_date)} title+date entries, and {len(dogc_by_title_only)} titles.")

    print("\n--- Checking Unmatched Socrata Records against DOGC ---")
    
    stats_by_category = defaultdict(lambda: {"total_unmatched_in_cido": 0, "matched_in_dogc": 0, "still_unmatched": 0})
    matched_in_dogc_details = []
    
    for doc in bopl_docs:
        cat = doc.get("category") or "other"
        
        # Socrata items that did not come from CIDO local map
        is_cido_matched = doc.get("source_api") == "dadesobertes.seu-e.cat/cido" or bool(doc.get("cido_id"))
        
        if not is_cido_matched:
            stats_by_category[cat]["total_unmatched_in_cido"] += 1
            
            matched_dogc_id = None
            match_reason = None
            
            # Check DOGC match
            # 1. Match by URLs
            for u_key in ["link_to_text", "urlHtml", "urlPdf"]:
                u_val = doc.get(u_key)
                if u_val and isinstance(u_val, str) and u_val.strip() in dogc_by_url:
                    matched_dogc_id = dogc_by_url[u_val.strip()]
                    match_reason = f"dogc_url_match ({u_key})"
                    break
                    
            # 2. Match by title + date
            if not matched_dogc_id:
                t_norm = normalize_text(doc.get("title"))
                dt_norm = parse_date(doc.get("date"))
                if t_norm and dt_norm and (t_norm, dt_norm) in dogc_by_title_date:
                    matched_dogc_id = dogc_by_title_date[(t_norm, dt_norm)]
                    match_reason = "dogc_title_and_date_match"
                    
            # 3. Match by normalized title only
            if not matched_dogc_id:
                t_norm = normalize_text(doc.get("title"))
                if t_norm and len(t_norm) > 20 and t_norm in dogc_by_title_only:
                    matched_dogc_id = dogc_by_title_only[t_norm]
                    match_reason = "dogc_title_match"

            if matched_dogc_id:
                stats_by_category[cat]["matched_in_dogc"] += 1
                matched_in_dogc_details.append({
                    "socrata_document": doc,
                    "matched_dogc_id": matched_dogc_id,
                    "match_reason": match_reason
                })
            else:
                stats_by_category[cat]["still_unmatched"] += 1

    total_unmatched = sum(s["total_unmatched_in_cido"] for s in stats_by_category.values())
    total_dogc_matched = sum(s["matched_in_dogc"] for s in stats_by_category.values())
    overall_dogc_pct = (total_dogc_matched / total_unmatched * 100) if total_unmatched > 0 else 0

    report = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "summary": {
            "total_unmatched_socrata_records": total_unmatched,
            "total_matched_in_dogc": total_dogc_matched,
            "still_unmatched_anywhere": total_unmatched - total_dogc_matched,
            "dogc_match_percentage": round(overall_dogc_pct, 2)
        },
        "breakdown_by_category": dict(stats_by_category),
        "matched_in_dogc_sample": matched_in_dogc_details[:50]
    }

    print("\n=== DOGC MATCHING SUMMARY ===")
    print(f"Total Unmatched Socrata Records Checked: {total_unmatched}")
    print(f"Matched in DOGC Dataset               : {total_dogc_matched} ({overall_dogc_pct:.2f}%)")
    print(f"Still Unmatched Anywhere              : {total_unmatched - total_dogc_matched}")
    
    print("\nCategory Breakdown:")
    for cat, c_stats in stats_by_category.items():
        tot = c_stats["total_unmatched_in_cido"]
        m = c_stats["matched_in_dogc"]
        pct = (m / tot * 100) if tot > 0 else 0
        print(f"  {cat:<35}: {m}/{tot} matched in DOGC ({pct:.2f}%)")

    print(f"\nWriting full DOGC match report to {output_report}...")
    with open(output_report, "w", encoding="utf-8") as out:
        json.dump(report, out, indent=2, ensure_ascii=False)

    print(f"Saved {output_report} in {time.time() - start_time:.2f} seconds.")

if __name__ == "__main__":
    main()
