#!/usr/bin/env python3
"""
verify_cido_urls.py

Standalone script to check and update live URL reachability for cido_documents.json.
Runs independently from cido_documents.json generation so URL checking can be performed
at any convenient time (e.g. off-peak hours or scheduled cron jobs).
"""

import os
import sys
import json
import argparse
import requests
from concurrent.futures import ThreadPoolExecutor
from requests.adapters import HTTPAdapter
try:
    from urllib3.util import create_urllib3_context
except ImportError:
    from urllib3.util.ssl_ import create_urllib3_context

class CustomSSLAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        context = create_urllib3_context()
        context.set_ciphers('DEFAULT@SECLEVEL=1')
        kwargs['ssl_context'] = context
        return super(CustomSSLAdapter, self).init_poolmanager(*args, **kwargs)

def setup_session():
    session = requests.Session()
    session.mount('https://', CustomSSLAdapter())
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    })
    return session

def check_single_url(session, url, timeout=5):
    if not url:
        return False, None
    try:
        r = session.head(url, allow_redirects=True, timeout=timeout)
        if r.status_code in [200, 301, 302, 303, 307, 308]:
            return True, r.status_code
        r_get = session.get(url, allow_redirects=True, stream=True, timeout=timeout)
        r_get.close()
        if r_get.status_code == 200:
            return True, 200
        return False, r_get.status_code
    except Exception:
        return False, None

def verify_urls(input_file, output_file, max_workers=50, limit=None, verbose=True):
    if not os.path.exists(input_file):
        print(f"Error: Input file '{input_file}' does not exist.")
        return False

    if verbose:
        print(f"Loading '{input_file}'...")
    with open(input_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    session = setup_session()
    
    # Collect all URLs to check
    targets = []
    for item in data:
        for doc in item.get("documents", []):
            url = doc.get("urlPdf") or doc.get("urlHtml")
            if url:
                targets.append((doc, url))
                
    if limit:
        targets = targets[:limit]

    if verbose:
        print(f"Checking reachability for {len(targets)} URLs using {max_workers} parallel workers...")

    def process_target(t):
        doc, url = t
        is_active, code = check_single_url(session, url)
        doc["isUrlActive"] = is_active
        doc["urlStatus"] = "active" if is_active else "eliminated"
        doc["urlStatusCode"] = code
        return is_active

    active_count = 0
    eliminated_count = 0
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        results = executor.map(process_target, targets)
        for is_active in results:
            if is_active:
                active_count += 1
            else:
                eliminated_count += 1

    if verbose:
        print(f"\n[URL Audit Summary]")
        print(f"  - Total URLs Checked: {len(targets)}")
        print(f"  - Active URLs: {active_count}")
        print(f"  - Eliminated / Broken URLs: {eliminated_count}")

    out_path = output_file if output_file else input_file
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    if verbose:
        print(f"\nUpdated dataset saved to '{out_path}'.")
    return True

def main():
    parser = argparse.ArgumentParser(description="Standalone URL verification tool for cido_documents.json")
    parser.add_argument("--input", type=str, default="data/cido_documents.json", help="Path to input JSON dataset")
    parser.add_argument("--output", type=str, default=None, help="Path for output JSON dataset (defaults to overwrite input)")
    parser.add_argument("--workers", type=int, default=50, help="Number of concurrent thread workers")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of URLs to check for testing")
    args = parser.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    cat_root = os.path.dirname(script_dir)
    
    input_path = os.path.abspath(args.input if os.path.isabs(args.input) else os.path.join(cat_root, args.input))
    output_path = os.path.abspath(args.output) if args.output else input_path

    verify_urls(input_path, output_path, max_workers=args.workers, limit=args.limit)

if __name__ == "__main__":
    main()
