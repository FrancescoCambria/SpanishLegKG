import os
import re
import sys
import json
import time
import tempfile
import argparse
import requests
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

# Try importing Docling (only needed if --docling is used)
try:
    from docling.document_converter import DocumentConverter
    HAS_DOCLING = True
except ImportError:
    HAS_DOCLING = False

def fetch_law_urls(mode, urls_path, metadata_path):
    """
    Fetches new law URLs from Socrata API and updates the local lists.
    (Merged from get_html.py)
    """
    existing_urls = set()
    existing_metadata = []
    
    if os.path.exists(urls_path):
        with open(urls_path, "r", encoding="utf-8") as f:
            existing_urls = {line.strip() for line in f if line.strip()}
            
    if os.path.exists(metadata_path):
        try:
            with open(metadata_path, "r", encoding="utf-8") as f:
                existing_metadata = json.load(f)
        except Exception:
            existing_metadata = []

    if mode == "update" and existing_urls:
        print("Running Socrata URL fetch in incremental UPDATE mode...")
        socrata_url = "https://analisi.transparenciacatalunya.cat/resource/n6hn-rmy7.json?$limit=2000&$order=data_de_publicaci_del_diari DESC"
    else:
        print("Running Socrata URL fetch in BULK mode...")
        socrata_url = "https://analisi.transparenciacatalunya.cat/resource/n6hn-rmy7.json?$limit=50000"
        existing_urls = set()
        existing_metadata = []

    try:
        r = requests.get(socrata_url, timeout=60)
        if r.status_code != 200:
            print(f"[Error] Socrata API returned status code {r.status_code}")
            return
        data = r.json()
    except Exception as e:
        print(f"[Error] Failed to fetch Socrata dataset: {e}")
        return

    new_urls = []
    new_metadata = []
    
    for record in data:
        format_html = record.get("format_html") or {}
        html_url = format_html.get("url") or (record.get("url_ltima_versi_format_html") or {}).get("url")
        
        if html_url and html_url not in existing_urls:
            doc_type = record.get("rang_de_norma", "")
            year = record.get("any", "")
            
            new_urls.append(html_url)
            new_metadata.append({
                "url": html_url,
                "type": doc_type,
                "year": str(year)
            })
            existing_urls.add(html_url)

    if new_urls:
        print(f"Found {len(new_urls)} new URLs.")
        os.makedirs(os.path.dirname(urls_path), exist_ok=True)
        
        if mode == "update":
            with open(urls_path, "a", encoding="utf-8") as f:
                for u in new_urls:
                    f.write(u + "\n")
            final_metadata = existing_metadata + new_metadata
            with open(metadata_path, "w", encoding="utf-8") as f:
                json.dump(final_metadata, f, indent=2, ensure_ascii=False)
        else:
            with open(urls_path, "w", encoding="utf-8") as f:
                for u in new_urls:
                    f.write(u + "\n")
            with open(metadata_path, "w", encoding="utf-8") as f:
                json.dump(new_metadata, f, indent=2, ensure_ascii=False)
                
        print(f"URLs and metadata files successfully updated. Total URLs in corpus: {len(existing_urls)}")
    else:
        print("No new laws found. Files are up to date.")

def get_filename_from_url(url, suffix=""):
    parsed_url = urlparse(url)
    path_parts = [p for p in parsed_url.path.split('/') if p]
    if 'eli' in path_parts:
        eli_index = path_parts.index('eli')
        filename_parts = path_parts[eli_index + 1:]
    else:
        filename_parts = path_parts
    
    clean_filename = "_".join(filename_parts)
    clean_filename = re.sub(r'[^a-zA-Z0-9_\-]', '', clean_filename)
    if not clean_filename:
        clean_filename = "scraped_law"
    return f"{clean_filename}{suffix}.txt"

def process_law_pages(urls_list, save_html, save_docling, html_out_dir, docling_out_dir):
    """
    Downloads law HTML pages using Playwright and converts them.
    (Merged from process_html.py and docling_html.py)
    """
    if not save_html and not save_docling:
        print("Nothing to process: Both --html and --docling are disabled.")
        return

    if save_docling and not HAS_DOCLING:
        print("[Error] Docling package is not installed. Cannot run Docling conversion.")
        print("Please install docling or run with --html only.")
        sys.exit(1)

    converter = DocumentConverter() if save_docling else None

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        
        for idx, url in enumerate(urls_list, 1):
            try:
                print(f"[{idx}/{len(urls_list)}] Processing: {url} ...")
                page = browser.new_page()
                
                # Navigate
                page.goto(url)
                
                # Wait until the document text container is visible and populated
                page.wait_for_selector("#fullText:not(:empty)", timeout=15000)
                
                # Expand all dynamic collapse sections (Afectacions, Descriptors, etc.)
                print("  Expanding dynamic panels (Afectacions, Descriptors, etc.)...")
                page.evaluate("""
                    jQuery('a[data-toggle="collapse"]').each(function() {
                        var $link = jQuery(this);
                        if ($link.is(':visible') && $link.hasClass('collapsed')) {
                            $link.click();
                        }
                    });
                """)
                
                # Wait for background requests to complete
                page.wait_for_load_state("networkidle")
                time.sleep(1) # Buffer for DOM rendering
                
                # Copy modals list to sidebar
                page.evaluate("""
                    var $modalPassive = jQuery('#modal_affectations_passive_ul');
                    if ($modalPassive.length && $modalPassive.children().length > 0) {
                        var $sidebarPassive = jQuery('#affectations_passive_ul');
                        if ($sidebarPassive.length) {
                            $sidebarPassive.empty().append($modalPassive.children().clone());
                        }
                    }
                    var $modalActive = jQuery('#modal_affectations_active_ul');
                    if ($modalActive.length && $modalActive.children().length > 0) {
                        var $sidebarActive = jQuery('#affectations_active_ul');
                        if ($sidebarActive.length) {
                            $sidebarActive.empty().append($modalActive.children().clone());
                        }
                    }
                    jQuery('li.veureMes').remove();
                """)
                
                html_content = page.content()
                page.close()

                # Parse and clean HTML
                soup = BeautifulSoup(html_content, 'html.parser')
                doc_id_input = soup.find('input', {'id': 'documentIdRequest'})
                doc_id = doc_id_input.get('value') if doc_id_input else "Unknown"

                # 1. Save Prettified HTML if requested
                if save_html:
                    os.makedirs(html_out_dir, exist_ok=True)
                    html_filename = get_filename_from_url(url)
                    html_filepath = os.path.join(html_out_dir, html_filename)
                    with open(html_filepath, 'w', encoding='utf-8') as f:
                        f.write(soup.prettify())
                    print(f"  Saved HTML to {html_filepath}")

                # 2. Convert and Save Docling Markdown if requested
                if save_docling:
                    os.makedirs(docling_out_dir, exist_ok=True)
                    # Fix invalid HTML structures to make sure Docling parses lists correctly
                    fixed_html = re.sub(r'<ol>\s*(<a[^>]*>.*?</a>)\s*</ol>', r'<li>\1</li>', html_content, flags=re.DOTALL)
                    
                    with tempfile.NamedTemporaryFile(suffix=".html", mode="w", encoding="utf-8", delete=False) as temp_file:
                        temp_file.write(fixed_html)
                        temp_filepath = temp_file.name
                    
                    try:
                        result = converter.convert(temp_filepath)
                        markdown_text = result.document.export_to_markdown()
                        
                        # Prepend metadata header
                        header = f"---\nDocument ID: {doc_id}\nURL: {url}\n---\n\n"
                        final_text = header + markdown_text
                        
                        docling_filename = get_filename_from_url(url, "_docling")
                        docling_filepath = os.path.join(docling_out_dir, docling_filename)
                        with open(docling_filepath, 'w', encoding='utf-8') as f:
                            f.write(final_text)
                        print(f"  Saved Docling Markdown to {docling_filepath}")
                    finally:
                        if os.path.exists(temp_filepath):
                            os.remove(temp_filepath)

            except Exception as e:
                print(f"  [Error] Failed to process {url}: {e}")
            
            # Politeness delay
            if idx < len(urls_list):
                time.sleep(2)
                
        browser.close()

def main():
    parser = argparse.ArgumentParser(description="Catalonian Laws Processing Pipeline")
    parser.add_argument("--fetch-urls", action="store_true", help="Fetch new law URLs from Socrata API first")
    parser.add_argument("--fetch-mode", choices=["update", "bulk"], default="update", help="Socrata API fetch mode (default: update)")
    
    parser.add_argument("--html", action="store_true", help="Save prettified HTML files of laws")
    parser.add_argument("--docling", action="store_true", help="Save converted Markdown files using Docling")
    
    parser.add_argument("--urls-file", type=str, default="old_files/law_urls.txt", help="Path to input law URLs file")
    parser.add_argument("--metadata-file", type=str, default="old_files/law_metadata.json", help="Path to law metadata file")
    
    parser.add_argument("--html-dir", type=str, default="data/html_output", help="Directory to save HTML outputs")
    parser.add_argument("--docling-dir", type=str, default="data/docling_output", help="Directory to save Docling outputs")
    
    parser.add_argument("--limit-pages", type=int, default=None, help="Maximum number of pages to download/process")
    args = parser.parse_args()

    # Since this script resides in a subdirectory, resolve paths relative to the parent Catalonia root
    script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    urls_path = os.path.join(script_dir, args.urls_file)
    metadata_path = os.path.join(script_dir, args.metadata_file)
    html_out_dir = os.path.join(script_dir, args.html_dir)
    docling_out_dir = os.path.join(script_dir, args.docling_dir)

    # By default, if neither output is specified, perform both HTML download and Docling conversion
    save_html = args.html
    save_docling = args.docling
    if not save_html and not save_docling:
        save_html = True
        save_docling = True

    # 1. Fetch URLs from Socrata if requested
    if args.fetch_urls:
        fetch_law_urls(args.fetch_mode, urls_path, metadata_path)

    # 2. Load URLs to process
    law_urls = []
    if os.path.exists(urls_path):
        with open(urls_path, "r", encoding="utf-8") as f:
            law_urls = [line.strip() for line in f if line.strip()]
            
    if not law_urls:
        print(f"No URLs found to process in {urls_path}.")
        print("Please run with --fetch-urls to fetch URLs first or add them to the file.")
        sys.exit(0)

    if args.limit_pages:
        law_urls = law_urls[:args.limit_pages]
        print(f"Limiting processing to the first {len(law_urls)} URLs.")

    # 3. Process the pages
    print(f"Starting pipeline on {len(law_urls)} pages...")
    process_law_pages(law_urls, save_html, save_docling, html_out_dir, docling_out_dir)
    print("Pipeline completed successfully.")

if __name__ == "__main__":
    main()
