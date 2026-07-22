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

def main():
    start_time = time.time()
    cat_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data_dir = os.path.join(cat_root, "data")
    
    bopl_path = os.path.join(data_dir, "bopl_documents.json")
    cido_map_path = os.path.join(data_dir, "cido_to_dogc_map.json")
    report_path = os.path.join(data_dir, "bopl_cido_matching_report.json")
    
    if not os.path.exists(bopl_path):
        print(f"Error: {bopl_path} not found. Please run download_bopl_documents.py first.")
        sys.exit(1)
        
    if not os.path.exists(cido_map_path):
        print(f"Error: {cido_map_path} not found.")
        sys.exit(1)
        
    print(f"Loading BOPL (Lleida) documents from {bopl_path}...")
    with open(bopl_path, "r", encoding="utf-8") as f:
        bopl_docs = json.load(f)
    print(f"Loaded {len(bopl_docs)} BOPL document records.")

    print(f"Loading CIDO map dataset from {cido_map_path}...")
    with open(cido_map_path, "r", encoding="utf-8") as f:
        cido_map = json.load(f)
    print(f"Loaded {len(cido_map)} CIDO entries.")

    # Index CIDO map by cidoId, URLs, and title+date
    cido_by_id = {}
    cido_by_url = {}
    cido_by_title_date = {}
    
    for item in cido_map:
        cid = str(item.get("cidoId"))
        cido_by_id[cid] = item
        
        url_c = item.get("urlCido")
        if url_c:
            cido_by_url[url_c.strip()] = cid
            
        c_title_norm = normalize_text(item.get("title"))
        c_date = item.get("date")
        if c_title_norm and c_date:
            cido_by_title_date[(c_title_norm, c_date)] = cid

        docs = item.get("documents") or []
        for d in docs:
            u_html = d.get("urlHtml")
            u_pdf = d.get("urlPdf")
            if u_html:
                cido_by_url[u_html.strip()] = cid
            if u_pdf:
                cido_by_url[u_pdf.strip()] = cid
                
            d_desc_norm = normalize_text(d.get("descripcio"))
            d_date = d.get("dataPublicacio") or c_date
            if d_desc_norm and d_date:
                cido_by_title_date[(d_desc_norm, d_date)] = cid

    print(f"Indexed CIDO lookups: {len(cido_by_url)} unique URLs and {len(cido_by_title_date)} title+date combinations.")

    print("\n--- Checking BOPL Matches against CIDO Map ---")
    matched_records = []
    unmatched_records = []
    
    stats_by_category = defaultdict(lambda: {"total": 0, "matched": 0, "unmatched": 0})
    
    for doc in bopl_docs:
        category = doc.get("category") or "other"
        stats_by_category[category]["total"] += 1
        
        matched_cido_id = None
        match_reason = None
        
        # 1. Match by cido_id if present
        if doc.get("cido_id") and str(doc["cido_id"]) in cido_by_id:
            matched_cido_id = str(doc["cido_id"])
            match_reason = "direct_cido_id"
            
        # 2. Match by URLs
        if not matched_cido_id:
            for u_key in ["link_to_text", "urlHtml", "urlPdf", "urlCido"]:
                url_val = doc.get(u_key)
                if url_val and url_val.strip() in cido_by_url:
                    matched_cido_id = cido_by_url[url_val.strip()]
                    match_reason = f"url_match ({u_key})"
                    break
                    
        # 3. Match by normalized title + publication date
        if not matched_cido_id:
            title_norm = normalize_text(doc.get("title") or doc.get("record_title"))
            d_date = doc.get("date")
            if title_norm and d_date and (title_norm, d_date) in cido_by_title_date:
                matched_cido_id = cido_by_title_date[(title_norm, d_date)]
                match_reason = "title_and_date_match"
                
        if matched_cido_id:
            stats_by_category[category]["matched"] += 1
            matched_records.append({
                "document": doc,
                "matched_cido_id": matched_cido_id,
                "match_reason": match_reason
            })
        else:
            stats_by_category[category]["unmatched"] += 1
            unmatched_records.append(doc)

    total_docs = len(bopl_docs)
    total_matched = len(matched_records)
    total_unmatched = len(unmatched_records)
    match_percentage = (total_matched / total_docs * 100) if total_docs > 0 else 0

    report = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "bulletin": "BOPL (Diputació de Lleida)",
        "summary": {
            "total_bopl_documents_checked": total_docs,
            "total_matched_in_cido": total_matched,
            "total_unmatched_in_cido": total_unmatched,
            "match_percentage": round(match_percentage, 2)
        },
        "breakdown_by_category": dict(stats_by_category),
        "matched_documents_sample": matched_records[:50],
        "unmatched_documents_sample": unmatched_records[:50]
    }

    print(f"\n--- BOPL Matching Summary ---")
    print(f"Total BOPL (Lleida) Documents Checked: {total_docs}")
    print(f"Matched in CIDO Map                  : {total_matched} ({match_percentage:.2f}%)")
    print(f"Unmatched                             : {total_unmatched}")
    
    print("\nCategory Breakdown:")
    for cat, c_stats in stats_by_category.items():
        pct = (c_stats["matched"] / c_stats["total"] * 100) if c_stats["total"] > 0 else 0
        print(f"  {cat:<30}: {c_stats['matched']}/{c_stats['total']} matched ({pct:.2f}%)")

    print(f"\nWriting full match report to {report_path}...")
    with open(report_path, "w", encoding="utf-8") as out:
        json.dump(report, out, indent=2, ensure_ascii=False)

    print(f"Saved {report_path} in {time.time() - start_time:.2f} seconds.")

if __name__ == "__main__":
    main()
