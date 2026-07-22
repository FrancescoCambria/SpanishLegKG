import os
import re
import sys
import json
import tempfile
import subprocess
import argparse
import requests
from concurrent.futures import ThreadPoolExecutor

# Add pipeline_scripts and Catalonia root to sys.path
script_dir = os.path.dirname(os.path.abspath(__file__))
cat_root = os.path.dirname(script_dir)
for d in [script_dir, cat_root]:
    if d not in sys.path:
        sys.path.append(d)

from generate_cido_map import normalize_title, load_dogc_reference_data
from get_dogc_documents import setup_session, extract_documents_from_summary

def extract_text_from_pdf_bytes(pdf_bytes):
    """
    Extracts text from PDF bytes using pdftotext.
    """
    if not pdf_bytes:
        return ""
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(pdf_bytes)
            tmp_path = tmp.name
        res = subprocess.run(['pdftotext', tmp_path, '-'], capture_output=True, text=True)
        try:
            os.remove(tmp_path)
        except Exception:
            pass
        if res.returncode == 0 and res.stdout:
            return res.stdout
    except Exception:
        pass
    return ""

def extract_issue_num_from_url(url, fallback_num=None):
    if fallback_num and str(fallback_num).isdigit():
        return str(fallback_num)
    if not url:
        return None
    # Check /PDF/9465/2102101.pdf pattern
    m = re.search(r'/PDF/(\d+)/', url)
    if m:
        return m.group(1)
    # Check numDOGC parameter
    m = re.search(r'numDOGC=(\d+)', url)
    if m:
        return m.group(1)
    # Check DOGC_YYYYMMDD_... pattern
    m = re.search(r'DOGC_\d{8}_(\d+)_', url)
    if m:
        return str(int(m.group(1)))
    return None

def extract_year_from_doc(doc_entry, record):
    pub_date = doc_entry.get("dataPublicacio") or record.get("date") or ""
    if len(pub_date) >= 4 and pub_date[:4].isdigit():
        return int(pub_date[:4])
    # check date in URL if format YYYYMMDD
    url = doc_entry.get("urlPdf") or ""
    m = re.search(r'/(\d{4})/\d{2}/\d{2}/', url)
    if m:
        return int(m.group(1))
    return 0

def clean_org_name(name):
    if not name:
        return ""
    t = normalize_title(name)
    prefixes = ['ajuntament de', 'ajuntament d', 'consell comarcal de', 'consell comarcal d', 'consorci del', 'consorci de', 'consorci d']
    for p in prefixes:
        if t.startswith(p):
            t = t[len(p):].strip()
    return t

def resolve_single_unresolved_dogc(session, summary_cache, cido_record, doc_entry, dogc_by_id, verbose=False):
    """
    Resolves an unresolved DOGC document by downloading its PDF, querying summaryDOGC API,
    and matching the title/text to obtain documentId and htmlUrl.
    """
    doc_entry['attemptedResolution'] = True

    if doc_entry.get('matchingDogcRecord') is not None and doc_entry.get('dogcDocumentId'):
        return False, None

    pdf_url = doc_entry.get('urlPdf')
    num_butlleti = doc_entry.get('numButlleti')
    issue_num = extract_issue_num_from_url(pdf_url, fallback_num=num_butlleti)

    if not issue_num:
        return False, None

    # Fetch summary for issue_num if not cached
    if issue_num not in summary_cache:
        try:
            r_sum = session.post('https://portaldogc.gencat.cat/eadop-rest/api/dogc/summaryDOGC', 
                                 data={'numDOGC': str(issue_num), 'language': 'ca'}, timeout=15)
            if r_sum.status_code == 200:
                _, docs = extract_documents_from_summary(r_sum.json())
                summary_cache[issue_num] = docs
            else:
                summary_cache[issue_num] = []
        except Exception as e:
            if verbose:
                print(f"Error querying summaryDOGC for issue {issue_num}: {e}")
            summary_cache[issue_num] = []

    issue_docs = summary_cache.get(issue_num) or []
    if not issue_docs:
        return False, None

    pdf_text = ""
    pdf_bytes = None
    if pdf_url:
        try:
            r_pdf = session.get(pdf_url, timeout=20)
            if r_pdf.status_code == 200 and r_pdf.content:
                pdf_bytes = r_pdf.content
                pdf_text = extract_text_from_pdf_bytes(pdf_bytes)
        except Exception as e:
            if verbose:
                print(f"Error downloading PDF {pdf_url}: {e}")

    norm_pdf_text = normalize_title(pdf_text) if pdf_text else ""
    record_title = cido_record.get('title') or ""
    norm_rec_title = normalize_title(record_title)

    matched_doc = None

    # Strategy 1: Match against title inside PDF text
    if norm_pdf_text:
        for d in issue_docs:
            d_title = d.get('title') or ""
            norm_d_title = normalize_title(d_title)
            if norm_d_title and len(norm_d_title) >= 15 and norm_d_title in norm_pdf_text:
                matched_doc = d
                break

    # Strategy 2: Match against record title
    if not matched_doc and norm_rec_title:
        for d in issue_docs:
            d_title = d.get('title') or ""
            norm_d_title = normalize_title(d_title)
            if norm_d_title and (norm_d_title in norm_rec_title or norm_rec_title in norm_d_title):
                matched_doc = d
                break

    # Strategy 3: Check CVE match if CVE is present in PDF text and summary doc
    if not matched_doc and norm_pdf_text:
        m_cve = re.search(r'CVE-DOGC-[A-Z]-\d+-\d+', pdf_text)
        if m_cve:
            cve_str = m_cve.group(0)
            for d in issue_docs:
                if cve_str in (d.get('title') or '') or cve_str in (d.get('cve') or ''):
                    matched_doc = d
                    break

    # Strategy 4: Organism / Institution & keyword search in candidate PDFs from summary
    if not matched_doc:
        inst = cido_record.get('institucio') or ""
        clean_inst = clean_org_name(inst)
        keywords = [w for w in norm_rec_title.split() if len(w) >= 5]
        
        if clean_inst and keywords:
            for d in issue_docs:
                clean_d_org = clean_org_name(d.get('organisme') or "")
                clean_d_title = clean_org_name(d.get('title') or "")
                
                if (clean_inst and (clean_inst in clean_d_org or clean_d_org in clean_inst or clean_inst in clean_d_title)):
                    cand_pdf_url = d.get('pdfUrl')
                    if cand_pdf_url:
                        try:
                            r_cand = session.get(cand_pdf_url, timeout=10)
                            if r_cand.status_code == 200 and r_cand.content:
                                cand_text = normalize_title(extract_text_from_pdf_bytes(r_cand.content))
                                if any(kw in cand_text for kw in keywords):
                                    matched_doc = d
                                    break
                        except Exception:
                            pass

    if matched_doc:
        doc_id = matched_doc.get('documentId')
        doc_entry['appearsInDogc'] = True
        doc_entry['dogcDocumentId'] = doc_id
        if matched_doc.get('htmlUrl'):
            doc_entry['urlHtml'] = matched_doc.get('htmlUrl')
        doc_entry['matchingDogcRecord'] = {
            'documentId': doc_id,
            'title': matched_doc.get('title'),
            'dateDOGC': matched_doc.get('dateDOGC'),
            'organisme': matched_doc.get('organisme')
        }
        return True, matched_doc

    return False, None

def main():
    parser = argparse.ArgumentParser(description="Optimized resolution of unresolved DOGC documents using PDF text parsing and summaryDOGC matching")
    parser.add_argument("--limit", type=int, default=200, help="Maximum number of unresolved DOGC entries to process (default: 200, set 0 for all)")
    parser.add_argument("--min-year", type=int, default=2005, help="Minimum publication year to attempt matching (default: 2005, skips pre-2005 scanned PDFs)")
    parser.add_argument("--force", action="store_true", help="Re-attempt entries that were previously flagged as attempted")
    parser.add_argument("--input", type=str, default="data/cido_to_dogc_map.json", help="Path to cido_to_dogc_map.json")
    parser.add_argument("--dogc-json", type=str, default="data/dogc_documents.json", help="Path to dogc_documents.json")
    parser.add_argument("--csv-output", type=str, default="data/cido_to_dogc_map.csv", help="Path to save updated CSV summary")
    parser.add_argument("--verbose", action="store_true", help="Print verbose resolution logs")
    args = parser.parse_args()

    cat_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data_dir = os.path.join(cat_root, "data")
    map_json_path = os.path.join(cat_root, args.input)
    dogc_json_path = os.path.join(cat_root, args.dogc_json)
    csv_output_path = os.path.join(cat_root, args.csv_output)
    summary_cache_path = os.path.join(data_dir, ".dogc_summary_cache.json")

    if not os.path.exists(map_json_path):
        print(f"Error: Mapping JSON file {map_json_path} not found.")
        sys.exit(1)

    print(f"Loading mapping records from {map_json_path}...")
    with open(map_json_path, "r", encoding="utf-8") as f:
        cido_mappings = json.load(f)

    dogc_by_id, _ = load_dogc_reference_data(data_dir, verbose=args.verbose)
    existing_dogc_list = []
    if os.path.exists(dogc_json_path):
        with open(dogc_json_path, "r", encoding="utf-8") as f:
            existing_dogc_list = json.load(f)

    summary_cache = {}
    if os.path.exists(summary_cache_path):
        try:
            with open(summary_cache_path, "r", encoding="utf-8") as sc_file:
                summary_cache = json.load(sc_file)
            print(f"Loaded {len(summary_cache)} cached DOGC issue summaries from disk.")
        except Exception:
            pass

    # Collect target unresolved DOGC document entries with year filtering & attempt tracking
    unresolved_targets = []
    skipped_pre_min_year = 0
    skipped_already_attempted = 0

    for r in cido_mappings:
        docs = r.get("documents") or []
        for d in docs:
            b = d.get("butlleti")
            if (b == "DOGC" or d.get("appearsInDogc")) and not d.get("matchingDogcRecord"):
                yr = extract_year_from_doc(d, r)
                if yr > 0 and yr < args.min_year:
                    skipped_pre_min_year += 1
                    continue
                if d.get("attemptedResolution") and not args.force:
                    skipped_already_attempted += 1
                    continue
                unresolved_targets.append((r, d))

    print(f"Found {len(unresolved_targets)} eligible unresolved DOGC targets (Skipped {skipped_pre_min_year} pre-{args.min_year} entries, {skipped_already_attempted} previously attempted entries).")

    if args.limit > 0:
        unresolved_targets = unresolved_targets[:args.limit]
        print(f"Processing up to {len(unresolved_targets)} targets in this run...")

    session = setup_session()
    resolved_count = 0
    new_dogc_records = []

    for i, (cido_rec, doc_entry) in enumerate(unresolved_targets, 1):
        c_id = cido_rec.get("cidoId")
        title = cido_rec.get("title") or "Unknown"
        if args.verbose or i % 10 == 0:
            print(f"[{i}/{len(unresolved_targets)}] Resolving CIDO {c_id} | {title[:50]}...")

        success, matched_doc = resolve_single_unresolved_dogc(
            session, summary_cache, cido_rec, doc_entry, dogc_by_id, verbose=args.verbose
        )

        if success and matched_doc:
            resolved_count += 1
            doc_id = matched_doc.get("documentId")
            if doc_id and str(doc_id) not in dogc_by_id:
                dogc_by_id[str(doc_id)] = matched_doc
                new_dogc_records.append(matched_doc)
                existing_dogc_list.append(matched_doc)

    print(f"\nResolution completed! Successfully resolved {resolved_count}/{len(unresolved_targets)} DOGC documents.")

    # Save summary cache to disk
    try:
        with open(summary_cache_path, "w", encoding="utf-8") as sc_file:
            json.dump(summary_cache, sc_file, ensure_ascii=False)
    except Exception as e:
        print(f"Warning: Could not save summary cache to disk: {e}")

    if new_dogc_records:
        print(f"Adding {len(new_dogc_records)} newly discovered documents to {dogc_json_path}...")
        with open(dogc_json_path, "w", encoding="utf-8") as out:
            json.dump(existing_dogc_list, out, indent=2, ensure_ascii=False)

    print(f"Saving updated mapping JSON to {map_json_path}...")
    with open(map_json_path, "w", encoding="utf-8") as out:
        json.dump(cido_mappings, out, indent=2, ensure_ascii=False)

    print("Updating CSV statistics...")
    import csv
    headers = [
        "Module Type", "Total Records", "Records with 0 Docs", "Records with 1 Doc", "Records with >1 Docs",
        "Resolved DOGC Docs", "Unresolved DOGC Docs", "Other Sources Docs", "Total Docs"
    ]
    by_type = {}
    for r in cido_mappings:
        m_type = r.get("type") or "unknown"
        if m_type not in by_type:
            by_type[m_type] = []
        by_type[m_type].append(r)

    totals = {
        "Module Type": "Total / All Modules",
        "Total Records": 0, "Records with 0 Docs": 0, "Records with 1 Doc": 0, "Records with >1 Docs": 0,
        "Resolved DOGC Docs": 0, "Unresolved DOGC Docs": 0, "Other Sources Docs": 0, "Total Docs": 0
    }
    with open(csv_output_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for m_type in sorted(by_type.keys()):
            group = by_type[m_type]
            total_rec = len(group)
            zeros, ones, manys = 0, 0, 0
            resolved, unresolved, others = 0, 0, 0
            for r in group:
                docs = r.get("documents") or []
                num_docs = len(docs)
                if num_docs == 0: zeros += 1
                elif num_docs == 1: ones += 1
                else: manys += 1
                for d in docs:
                    b = d.get("butlleti")
                    if b == "DOGC":
                        if d.get("appearsInDogc") and d.get("matchingDogcRecord"):
                            resolved += 1
                        else:
                            unresolved += 1
                    else:
                        others += 1
            tot_docs = resolved + unresolved + others
            row = {
                "Module Type": m_type, "Total Records": total_rec,
                "Records with 0 Docs": zeros, "Records with 1 Doc": ones, "Records with >1 Docs": manys,
                "Resolved DOGC Docs": resolved, "Unresolved DOGC Docs": unresolved, "Other Sources Docs": others,
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
    print("CSV updated successfully.")

if __name__ == "__main__":
    main()
