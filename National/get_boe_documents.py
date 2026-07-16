#!/usr/bin/env python3
"""
BOE Document Crawler and Updater
Downloads legal documents (metadata and XML) from the Boletín Oficial del Estado (BOE)
using its official OpenData REST API (https://www.boe.es/datosabiertos/api/) and updates
incremental additions upon subsequent runs.
"""

import os
import sys
import json
import time
import argparse
from datetime import datetime, timedelta
import requests

def setup_session():
    """
    Sets up a requests Session with standard headers and connection pooling.
    """
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json"
    })
    return session

def parse_date(date_str):
    """
    Parses date string in YYYY-MM-DD or YYYYMMDD format to a datetime.date object.
    """
    date_str = date_str.replace("-", "").strip()
    try:
        return datetime.strptime(date_str, "%Y%m%d").date()
    except ValueError:
        raise argparse.ArgumentTypeError(f"Invalid date format: '{date_str}'. Use YYYY-MM-DD or YYYYMMDD.")

def fetch_sumario(session, date_str, max_retries=3):
    """
    Fetches the BOE summary for a specific date (YYYYMMDD) from the official API.
    Returns the JSON data, None on 404 (no publication), or raises exception on persistent error.
    """
    url = f"https://www.boe.es/datosabiertos/api/boe/sumario/{date_str}"
    backoff = 1.0
    
    for attempt in range(max_retries):
        try:
            response = session.get(url, timeout=20)
            if response.status_code == 200:
                try:
                    return response.json()
                except ValueError:
                    # Sometimes errors are returned in XML even when JSON was requested
                    if b"La informaci" in response.content or b"404" in response.content:
                        return None
                    raise Exception("Response is not valid JSON")
            elif response.status_code == 404:
                return None
            else:
                print(f"\n[Warning] API returned status {response.status_code} for {date_str} (Attempt {attempt+1}/{max_retries})")
        except Exception as e:
            print(f"\n[Warning] Connection error for {date_str} on attempt {attempt+1}/{max_retries}: {e}")
            
        if attempt < max_retries - 1:
            time.sleep(backoff)
            backoff *= 2.0
            
    raise Exception(f"Failed to retrieve data for {date_str} after {max_retries} attempts.")

def download_file(session, url, target_path, is_binary=False, max_retries=3):
    """
    Downloads a file (XML or PDF) from the given URL and saves it to target_path.
    """
    backoff = 1.0
    for attempt in range(max_retries):
        try:
            if is_binary:
                response = session.get(url, timeout=30, stream=True)
                if response.status_code == 200:
                    with open(target_path, "wb") as f:
                        for chunk in response.iter_content(chunk_size=8192):
                            if chunk:
                                f.write(chunk)
                    return True
            else:
                response = session.get(url, timeout=20)
                if response.status_code == 200:
                    with open(target_path, "wb") as f:
                        f.write(response.content)
                    return True
            
            print(f"\n[Warning] Failed to download {url} (Status: {response.status_code}) (Attempt {attempt+1}/{max_retries})")
        except Exception as e:
            print(f"\n[Warning] Error downloading {url} on attempt {attempt+1}/{max_retries}: {e}")
            
        if attempt < max_retries - 1:
            time.sleep(backoff)
            backoff *= 2.0
            
    return False

def extract_documents_from_sumario(sumario_data, sections_filter=None):
    """
    Traverses the BOE sumario JSON and extracts document metadata.
    Optionally filters by section code (e.g. ['1'] for Section I).
    """
    docs = []
    sumario = sumario_data.get("data", {}).get("sumario", {})
    metadatos = sumario.get("metadatos", {})
    fecha_pub = metadatos.get("fecha_publicacion") # YYYYMMDD
    
    diarios = sumario.get("diario", [])
    if isinstance(diarios, dict):
        diarios = [diarios]
    elif not isinstance(diarios, list):
        diarios = []
        
    for d in diarios:
        diario_num = d.get("numero")
        secciones = d.get("seccion", [])
        if isinstance(secciones, dict):
            secciones = [secciones]
        elif not isinstance(secciones, list):
            secciones = []
            
        for sec in secciones:
            sec_codigo = str(sec.get("codigo", ""))
            sec_nombre = sec.get("nombre", "")
            
            # Apply section filter if provided (and not 'all')
            if sections_filter and "all" not in sections_filter:
                if sec_codigo not in sections_filter:
                    continue
            
            deps = sec.get("departamento", [])
            if isinstance(deps, dict):
                deps = [deps]
            elif not isinstance(deps, list):
                deps = []
                
            for dept in deps:
                dept_codigo = str(dept.get("codigo", ""))
                dept_nombre = dept.get("nombre", "")
                
                # We collect items here
                items = []
                
                # 1. From epigrafes
                epigs = dept.get("epigrafe", [])
                if isinstance(epigs, dict):
                    epigs = [epigs]
                elif epigs is None or not isinstance(epigs, list):
                    epigs = []
                    
                for epig in epigs:
                    epig_nombre = epig.get("nombre")
                    epig_items = epig.get("item", [])
                    if isinstance(epig_items, dict):
                        epig_items = [epig_items]
                    elif not isinstance(epig_items, list):
                        epig_items = []
                    for it in epig_items:
                        it["epigrafe_nombre"] = epig_nombre
                        items.append(it)
                        
                # 2. Directly under department
                dept_items = dept.get("item", [])
                if isinstance(dept_items, dict):
                    dept_items = [dept_items]
                elif not isinstance(dept_items, list):
                    dept_items = []
                for it in dept_items:
                    items.append(it)
                    
                # Store normalized document information
                for it in items:
                    identificador = it.get("identificador")
                    if not identificador:
                        continue
                        
                    # Skip sumario entries (like BOE-S-...) as they are index summaries, not documents
                    if identificador.startswith("BOE-S"):
                        continue
                        
                    pdf_info = it.get("url_pdf", {})
                    pdf_url = pdf_info.get("texto") if isinstance(pdf_info, dict) else pdf_info
                    
                    docs.append({
                        "identificador": identificador,
                        "control": it.get("control"),
                        "titulo": it.get("titulo"),
                        "fecha_publicacion": fecha_pub,
                        "diario_numero": diario_num,
                        "seccion_codigo": sec_codigo,
                        "seccion_nombre": sec_nombre,
                        "departamento_codigo": dept_codigo,
                        "departamento_nombre": dept_nombre,
                        "epigrafe_nombre": it.get("epigrafe_nombre"),
                        "pdf_url": pdf_url,
                        "html_url": it.get("url_html"),
                        "xml_url": it.get("url_xml"),
                    })
    return docs

def load_existing_database(filepath):
    """
    Loads existing database JSON if it exists, returning a list of documents.
    """
    if os.path.exists(filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
                else:
                    print(f"[Warning] Database file {filepath} is not a list. Starting fresh.")
        except Exception as e:
            print(f"[Warning] Failed to load database {filepath}: {e}. Starting fresh.")
    return []

def main():
    parser = argparse.ArgumentParser(
        description="BOE Document Crawler - Chronological Downloader and Incremental Updater",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Download Section I documents from 2026-07-01 to today:
  python3 get_boe_documents.py -s 2026-07-01
  
  # Download Section I & III documents for a specific year, downloading PDFs too:
  python3 get_boe_documents.py -s 2025-01-01 -e 2025-12-31 --sections 1,3 --download-pdf
  
  # Incremental update (runs from the latest date in database up to today):
  python3 get_boe_documents.py
        """
    )
    parser.add_argument("--start-date", "-s", type=parse_date, default=None,
                        help="Start date in YYYY-MM-DD or YYYYMMDD format. If omitted, resumes from the latest date in the database.")
    parser.add_argument("--end-date", "-e", type=parse_date, default=None,
                        help="End date in YYYY-MM-DD or YYYYMMDD format (defaults to today).")
    parser.add_argument("--output", "-o", type=str, default=None,
                        help="Path to output JSON database (defaults to data/boe_documents.json).")
    parser.add_argument("--xml-dir", "-x", type=str, default=None,
                        help="Directory to save downloaded XML documents (defaults to data/xml).")
    parser.add_argument("--pdf-dir", type=str, default=None,
                        help="Directory to save downloaded PDF documents (defaults to data/pdf).")
    parser.add_argument("--limit", "-l", type=int, default=None,
                        help="Maximum number of days/sumarios to crawl in this run.")
    parser.add_argument("--delay", "-d", type=float, default=0.2,
                        help="Politeness delay between requests in seconds (default: 0.2).")
    parser.add_argument("--sections", "--sec", type=str, default="1",
                        help="Comma-separated list of section codes (e.g. '1,3') or 'all' (default: '1' - Section I: Disposiciones generales).")
    parser.add_argument("--no-resume", action="store_true",
                        help="Start fresh, ignoring existing documents in the output database.")
    parser.add_argument("--download-xml", type=bool, default=True, action=argparse.BooleanOptionalAction,
                        help="Whether to download full XML for each document (default: True).")
    parser.add_argument("--download-pdf", action="store_true",
                        help="Whether to download PDF files for each document (default: False).")
    parser.add_argument("--overwrite", action="store_true",
                        help="Overwrite existing local files and duplicate metadata records.")
    
    args = parser.parse_args()
    
    script_dir = os.path.dirname(os.path.abspath(__file__))
    
    # Establish defaults for directories and files
    data_dir = os.path.join(script_dir, "data")
    if args.output is None:
        output_filepath = os.path.join(data_dir, "boe_documents.json")
    else:
        output_filepath = os.path.abspath(args.output)
        
    target_data_dir = os.path.dirname(output_filepath)
    os.makedirs(target_data_dir, exist_ok=True)
    
    xml_dirpath = os.path.abspath(args.xml_dir) if args.xml_dir else os.path.join(target_data_dir, "xml")
    pdf_dirpath = os.path.abspath(args.pdf_dir) if args.pdf_dir else os.path.join(target_data_dir, "pdf")
    
    failed_dates_path = os.path.join(target_data_dir, "failed_dates.txt")
    
    # Parse section filters
    sections_filter = [s.strip() for s in args.sections.split(",") if s.strip()]
    
    # Load database
    existing_docs = []
    if not args.no_resume:
        existing_docs = load_existing_database(output_filepath)
        
        # Migration logic for older database formats (flat xml/ and pdf/ directories)
        modified = False
        for doc in existing_docs:
            year = doc.get("fecha_publicacion", "")[:4]
            if not year:
                continue
                
            # Check if xml_path is flat, e.g., "xml/BOE-A-2026-XXXX.xml"
            if "xml_path" in doc and doc["xml_path"] and doc["xml_path"].startswith("xml/"):
                old_xml_rel = doc["xml_path"]
                old_xml_abs = os.path.join(target_data_dir, old_xml_rel)
                
                new_xml_rel = f"{year}/xml/{os.path.basename(old_xml_rel)}"
                new_xml_abs = os.path.join(target_data_dir, new_xml_rel)
                
                if os.path.exists(old_xml_abs):
                    os.makedirs(os.path.dirname(new_xml_abs), exist_ok=True)
                    os.rename(old_xml_abs, new_xml_abs)
                
                doc["xml_path"] = new_xml_rel
                modified = True
                
            # Check if pdf_path is flat, e.g., "pdf/BOE-A-2026-XXXX.pdf"
            if "pdf_path" in doc and doc["pdf_path"] and doc["pdf_path"].startswith("pdf/"):
                old_pdf_rel = doc["pdf_path"]
                old_pdf_abs = os.path.join(target_data_dir, old_pdf_rel)
                
                new_pdf_rel = f"{year}/pdf/{os.path.basename(old_pdf_rel)}"
                new_pdf_abs = os.path.join(target_data_dir, new_pdf_rel)
                
                if os.path.exists(old_pdf_abs):
                    os.makedirs(os.path.dirname(new_pdf_abs), exist_ok=True)
                    os.rename(old_pdf_abs, new_pdf_abs)
                    
                doc["pdf_path"] = new_pdf_rel
                modified = True
                
        if modified:
            print("Migrated existing database paths and files to year-based directory structure.")
            with open(output_filepath, "w", encoding="utf-8") as f:
                json.dump(existing_docs, f, indent=2, ensure_ascii=False)
                
            # Clean up old empty xml/pdf directories if they exist
            old_xml_dir = os.path.join(target_data_dir, "xml")
            old_pdf_dir = os.path.join(target_data_dir, "pdf")
            for path in [old_xml_dir, old_pdf_dir]:
                if os.path.exists(path) and os.path.isdir(path) and not os.listdir(path):
                    os.rmdir(path)
        
    existing_ids = set()
    latest_pub_date = None
    
    if existing_docs:
        existing_ids = {doc["identificador"] for doc in existing_docs if "identificador" in doc}
        # Find the latest publication date in database (format is YYYYMMDD)
        dates_in_db = [doc["fecha_publicacion"] for doc in existing_docs if doc.get("fecha_publicacion")]
        if dates_in_db:
            max_date_str = max(dates_in_db)
            try:
                latest_pub_date = datetime.strptime(max_date_str, "%Y%m%d").date()
            except ValueError:
                pass
                
    # Determine date range
    today = datetime.now().date()
    end_date = args.end_date if args.end_date else today
    
    start_date = args.start_date
    is_incremental = False
    
    if start_date is None:
        if latest_pub_date and not args.no_resume:
            # Resume from the latest publication date found in the database.
            # We start at latest_pub_date (inclusive) to capture any missed documents from that day.
            # Our item skipping logic will prevent duplicating items we already have.
            start_date = latest_pub_date
            is_incremental = True
            print(f"Incremental update: Resuming from the latest date in database: {start_date}")
        else:
            # Default fallback: last 30 days
            start_date = today - timedelta(days=30)
            print(f"No start date specified and no resume data found. Defaulting to last 30 days: {start_date}")
    else:
        print(f"Crawl range specified: {start_date} to {end_date}")
        
    if start_date > end_date:
        print(f"Start date {start_date} is after end date {end_date}. Nothing to do.")
        return
        
    session = setup_session()
    
    current_date = start_date
    days_processed = 0
    days_crawled_count = 0
    new_docs_added = 0
    skipped_days = 0
    
    print(f"Initializing crawl:")
    print(f"  Start Date:   {start_date}")
    print(f"  End Date:     {end_date}")
    print(f"  Sections:     {', '.join(sections_filter)}")
    print(f"  Output File:  {output_filepath}")
    print(f"  XML Save Dir: {xml_dirpath if args.download_xml else 'Disabled'}")
    print(f"  PDF Save Dir: {pdf_dirpath if args.download_pdf else 'Disabled'}")
    print(f"  Existing Database Records: {len(existing_docs)}")
    print(f"--------------------------------------------------")
    
    try:
        while current_date <= end_date:
            if args.limit and days_processed >= args.limit:
                print(f"\nLimit of {args.limit} publication days reached. Stopping.")
                break
                
            date_str = current_date.strftime("%Y%m%d")
            print(f"\rProcessing date: {current_date} (crawled: {days_crawled_count}, added: {new_docs_added})...", end="", flush=True)
            
            try:
                sumario_data = fetch_sumario(session, date_str)
            except Exception as e:
                print(f"\n[Error] Failed to fetch sumario for date {current_date}: {e}")
                # Save failed date to log
                with open(failed_dates_path, "a", encoding="utf-8") as ff:
                    ff.write(f"{current_date.strftime('%Y-%m-%d')}\n")
                
                # Move to next day
                current_date += timedelta(days=1)
                days_crawled_count += 1
                continue
                
            if sumario_data is None:
                # 404 meaning no publication on this date (Sundays, holidays, etc.)
                skipped_days += 1
                current_date += timedelta(days=1)
                days_crawled_count += 1
                continue
                
            # Extract document metadata
            docs_in_sumario = extract_documents_from_sumario(sumario_data, sections_filter)
            
            day_new_docs = 0
            for doc in docs_in_sumario:
                identificador = doc["identificador"]
                
                # Check for duplicates unless --overwrite is active
                if identificador in existing_ids and not args.overwrite:
                    continue
                    
                year = doc["fecha_publicacion"][:4]
                
                # Determine output directories for this year
                if args.xml_dir:
                    year_xml_dir = os.path.join(os.path.abspath(args.xml_dir), year)
                else:
                    year_xml_dir = os.path.join(target_data_dir, year, "xml")
                    
                if args.pdf_dir:
                    year_pdf_dir = os.path.join(os.path.abspath(args.pdf_dir), year)
                else:
                    year_pdf_dir = os.path.join(target_data_dir, year, "pdf")
                
                # Download XML if requested
                xml_downloaded = False
                if args.download_xml and doc["xml_url"]:
                    os.makedirs(year_xml_dir, exist_ok=True)
                    target_xml_path = os.path.join(year_xml_dir, f"{identificador}.xml")
                    if not os.path.exists(target_xml_path) or args.overwrite:
                        xml_downloaded = download_file(session, doc["xml_url"], target_xml_path, is_binary=False)
                        if xml_downloaded:
                            # Use relative path in JSON for portability
                            doc["xml_path"] = os.path.relpath(target_xml_path, target_data_dir)
                            if args.delay > 0:
                                time.sleep(args.delay)
                    else:
                        doc["xml_path"] = os.path.relpath(target_xml_path, target_data_dir)
                        xml_downloaded = True
                        
                # Download PDF if requested
                pdf_downloaded = False
                if args.download_pdf and doc["pdf_url"]:
                    os.makedirs(year_pdf_dir, exist_ok=True)
                    target_pdf_path = os.path.join(year_pdf_dir, f"{identificador}.pdf")
                    if not os.path.exists(target_pdf_path) or args.overwrite:
                        pdf_downloaded = download_file(session, doc["pdf_url"], target_pdf_path, is_binary=True)
                        if pdf_downloaded:
                            doc["pdf_path"] = os.path.relpath(target_pdf_path, target_data_dir)
                            if args.delay > 0:
                                time.sleep(args.delay)
                    else:
                        doc["pdf_path"] = os.path.relpath(target_pdf_path, target_data_dir)
                        pdf_downloaded = True
                
                # Update existing record if overwriting, otherwise append
                if identificador in existing_ids and args.overwrite:
                    # Remove old entry
                    existing_docs = [d for d in existing_docs if d.get("identificador") != identificador]
                    
                existing_docs.append(doc)
                existing_ids.add(identificador)
                day_new_docs += 1
                new_docs_added += 1
            
            # Save progress incrementally for every day that has new docs
            if day_new_docs > 0:
                with open(output_filepath, "w", encoding="utf-8") as f:
                    json.dump(existing_docs, f, indent=2, ensure_ascii=False)
                    
            days_processed += 1
            days_crawled_count += 1
            current_date += timedelta(days=1)
            
            # Politeness delay between days
            if args.delay > 0:
                time.sleep(args.delay)
                
    except KeyboardInterrupt:
        print("\n\nCrawl interrupted by user.")
    finally:
        # Final save
        with open(output_filepath, "w", encoding="utf-8") as f:
            json.dump(existing_docs, f, indent=2, ensure_ascii=False)
            
        print(f"\n\n==================== Run Summary ====================")
        print(f"Total days processed:      {days_processed}")
        print(f"Total days scanned:        {days_crawled_count} (including {skipped_days} skipped/non-publication days)")
        print(f"New documents added:       {new_docs_added}")
        print(f"Total documents in database: {len(existing_docs)}")
        print(f"Database saved to:         {output_filepath}")
        if args.download_xml:
            print(f"XML files directory:       {xml_dirpath}")
        if args.download_pdf:
            print(f"PDF files directory:       {pdf_dirpath}")
        print(f"=====================================================")

if __name__ == "__main__":
    main()
