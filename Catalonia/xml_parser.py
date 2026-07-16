import os
import sys
import re
import json
import argparse
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util import create_urllib3_context
from tqdm import tqdm

# Add Catalonia root directory to sys.path to import html_parser
script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path:
    sys.path.append(script_dir)

try:
    from html_parser import parse_document, get_spanish_url, build_affectation_text
except ImportError as e:
    print(f"Error importing from html_parser: {e}")
    sys.exit(1)

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
    "Referer": "https://dogc.gencat.cat/"
})

def scrape_xml_url_from_webpage(doc_id, url):
    """
    Scrapes the document webpage to find the Akoma Ntoso XML format link.
    """
    try:
        r = api_session.get(url, timeout=15)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, 'html.parser')
            for a in soup.find_all('a'):
                href = a.get('href')
                if href and 'format=xml' in href:
                    return requests.compat.urljoin(url, href)
    except Exception:
        pass
    return None

def fetch_api_data(doc_id, language="ca"):
    """
    Queries EADOP REST API endpoints for metadata, affectations, and descriptors.
    """
    try:
        # 1. Fetch main document text & metadata
        r_doc = api_session.post("https://portaldogc.gencat.cat/eadop-rest/api/dogc/documentDOGC", 
                                 data={"documentId": str(doc_id), "language": language}, timeout=15)
        if r_doc.status_code != 200:
            return None
        res_doc = r_doc.json() or {}
        
        # 2. Fetch affectations
        r_aff = api_session.post("https://portaldogc.gencat.cat/eadop-rest/api/dogc/getDocumentAffectations", 
                                 data={"documentId": str(doc_id), "language": language}, timeout=15)
        affectations = {"passive": [], "active": []}
        if r_aff.status_code == 200:
            res_aff_root = r_aff.json() or {}
            res_aff = res_aff_root.get("affectations") or {}
            
            passives = res_aff.get("passiveAffectations", {}).get("affectationList") or []
            for aff in passives:
                text = build_affectation_text(aff)
                target_doc = aff.get("description_document") or {}
                affectations["passive"].append({
                    "text": text,
                    "targetDocumentId": str(target_doc.get("documentId")) if target_doc.get("documentId") else None
                })
                
            actives = res_aff.get("activeAffectations", {}).get("affectationList") or []
            for aff in actives:
                text = build_affectation_text(aff)
                target_doc = aff.get("description_document") or {}
                affectations["active"].append({
                    "text": text,
                    "targetDocumentId": str(target_doc.get("documentId")) if target_doc.get("documentId") else None
                })
                
        # 3. Fetch descriptors
        r_desc = api_session.post("https://portaldogc.gencat.cat/eadop-rest/api/dogc/getDescriptorsDocumentDogc", 
                                  data={"documentId": str(doc_id), "language": language}, timeout=15)
        descriptors = {"organisms": [], "geographic": [], "thematic": []}
        if r_desc.status_code == 200:
            res_desc = r_desc.json() or {}
            for item in res_desc.get("organizationDescriptor") or []:
                descriptors["organisms"].append({"id": item.get("thesaurusId"), "name": item.get("title")})
            for item in res_desc.get("geographicDescriptor") or []:
                descriptors["geographic"].append({"id": item.get("thesaurusId"), "name": item.get("title")})
            for item in res_desc.get("thematicDescriptor") or []:
                descriptors["thematic"].append({"id": item.get("thesaurusId"), "name": item.get("title")})
                
        return {
            "res_doc": res_doc,
            "affectations": affectations,
            "descriptors": descriptors
        }
    except Exception:
        return None

def parse_xml_to_structured_json(doc_id, url, xml_content, api_data, language="ca"):
    """
    Parses Akoma Ntoso XML document contents and extracts structural data.
    """
    soup_xml = BeautifulSoup(xml_content, 'xml')
    
    # Extract HTML body from Akoma Ntoso XML content
    content_tag = soup_xml.find('content')
    html_body = ""
    if content_tag:
        period_val = content_tag.get('period') or ""
        # Akoma Ntoso frequently stores the entire HTML body in the period attribute
        if '<' in period_val and '>' in period_val:
            html_body = period_val
        else:
            html_body = content_tag.get_text()
            
    # Fallback to mainBody or body if content tag did not provide the html_body
    if not html_body:
        body_tag = soup_xml.find('mainBody') or soup_xml.find('body')
        if body_tag:
            html_body = body_tag.get_text()
            
    # Ultimate fallback: if res_doc is available, use its textDocument field
    if not html_body and api_data and api_data.get("res_doc"):
        html_body = api_data.get("res_doc", {}).get("textDocument") or ""
        
    eli_val = ""
    title_doc = ""
    res_doc = api_data.get("res_doc") if api_data else None
    
    if res_doc:
        eli_val = res_doc.get("uriELI", {}).get("link") if res_doc.get("uriELI") else ""
        title_doc = res_doc.get("titleDocument") or "Unknown Title"
    else:
        frbr_this = soup_xml.find('FRBRthis')
        eli_val = frbr_this.get('value') if frbr_this else ""
        preface_tag = soup_xml.find('preface')
        if preface_tag:
            title_doc = preface_tag.get('title') or preface_tag.get_text().strip()
            
    # Mock HTML structure to feed into parse_document
    mock_html = f"""
    <html>
      <body>
        <input type="hidden" id="documentIdRequest" value="{doc_id}">
        <div id="fullText">
          <span id="uriEli">URI ELI: {eli_val}</span>
          <h1>{title_doc}</h1>
          {html_body}
        </div>
      </body>
    </html>
    """
    
    parsed = parse_document(mock_html, url)
    if not parsed:
        return None
        
    # Enrich with REST API metadata if available
    if res_doc:
        parsed["eliUri"] = eli_val
        parsed["affectations"] = api_data.get("affectations") or {"passive": [], "active": []}
        parsed["descriptors"] = api_data.get("descriptors") or {"organisms": [], "geographic": [], "thematic": []}
        
        # Extract formats from EADOP linkDownload
        api_formats = {}
        link_download = res_doc.get("linkDownload") or {}
        if link_download.get("linkDownloadPDF"):
            api_formats["pdf"] = link_download["linkDownloadPDF"]
        if link_download.get("linkDownloadRDF"):
            api_formats["rdf"] = link_download["linkDownloadRDF"]
        if link_download.get("linkDownloadTTL"):
            api_formats["ttl"] = link_download["linkDownloadTTL"]
        if link_download.get("linkDownloadXML"):
            api_formats["xml"] = link_download["linkDownloadXML"]
        parsed["formats"] = api_formats
        
        text_adicional_obj = res_doc.get("textAdicional") or {}
        parsed["additionalText"] = text_adicional_obj.get("text")
        
        doc_data = res_doc.get("documentData") or {}
        
        parsed["metadata"] = {
            "typeOfLaw": doc_data.get("typeDocument") or "Unknown",
            "documentDate": doc_data.get("dateDocument") or "",
            "documentNumber": doc_data.get("numDocument") or "",
            "controlNumber": doc_data.get("numControl") or "",
            "emittingOrganism": doc_data.get("issuingAuthority") or "Unknown Emitting Organism",
            "cve": doc_data.get("CVE") or "",
            "dogcNumber": doc_data.get("numDOGC") or "",
            "dogcDate": doc_data.get("dateDOGC") or "",
            "dogcSection": doc_data.get("sectionDOGC") or ""
        }
        
    return parsed

def process_single_doc_xml(doc, xml_dir, structured_dir):
    """
    Downloads XML formats for a single document, saves them, and builds structured bilingual JSON.
    """
    doc_id = doc.get("documentId")
    if not doc_id:
        return False
        
    output_filename = f"dogc_doc_{doc_id}_structured.json"
    output_filepath = os.path.join(structured_dir, output_filename)
    
    xml_path_ca = os.path.join(xml_dir, f"dogc_doc_{doc_id}_ca.xml")
    xml_path_es = os.path.join(xml_dir, f"dogc_doc_{doc_id}_es.xml")
    
    # If both XML and structured JSON exist, skip to resume quickly
    if os.path.exists(output_filepath) and os.path.exists(xml_path_ca):
        return True
        
    url_ca = doc.get("htmlUrl")
    if not url_ca:
        url_ca = f"https://dogc.gencat.cat/ca/document-del-dogc/index.html?documentId={doc_id}"
    url_es = get_spanish_url(url_ca)
    
    try:
        # 1. Catalan XML Retrieval
        api_data_ca = fetch_api_data(doc_id, "ca")
        xml_url_ca = None
        if api_data_ca:
            xml_url_ca = api_data_ca.get("res_doc", {}).get("linkDownload", {}).get("linkDownloadXML")
            
        if not xml_url_ca:
            xml_url_ca = scrape_xml_url_from_webpage(doc_id, url_ca)
            
        if not xml_url_ca:
            xml_url_ca = f"https://portaldogc.gencat.cat/utilsEADOP/AppJava/AkomaNtoso?idNumber={doc_id}&format=xml"
            
        # Download and save Catalan XML
        r_xml_ca = api_session.get(xml_url_ca, timeout=20)
        if r_xml_ca.status_code == 200 and len(r_xml_ca.text) > 0:
            xml_content_ca = r_xml_ca.text
            with open(xml_path_ca, "w", encoding="utf-8") as f:
                f.write(xml_content_ca)
        else:
            return False
            
        # 2. Spanish XML Retrieval
        api_data_es = fetch_api_data(doc_id, "es")
        xml_url_es = None
        if api_data_es:
            xml_url_es = api_data_es.get("res_doc", {}).get("linkDownload", {}).get("linkDownloadXML")
            
        if not xml_url_es:
            xml_url_es = scrape_xml_url_from_webpage(doc_id, url_es)
            
        xml_content_es = None
        if xml_url_es:
            r_xml_es = api_session.get(xml_url_es, timeout=20)
            if r_xml_es.status_code == 200 and len(r_xml_es.text) > 0:
                xml_content_es = r_xml_es.text
                with open(xml_path_es, "w", encoding="utf-8") as f:
                    f.write(xml_content_es)
                    
        # 3. Parse XML content
        parsed_ca = parse_xml_to_structured_json(doc_id, url_ca, xml_content_ca, api_data_ca, "ca")
        if not parsed_ca:
            return False
            
        parsed_es = None
        if xml_content_es:
            parsed_es = parse_xml_to_structured_json(doc_id, url_es, xml_content_es, api_data_es, "es")
            
        # 4. Save bilingual JSON output (packaged exactly like batch_parse_recent.py)
        bilingual_data = {
            "documentId": doc_id,
            "eliUri": parsed_ca.get("eliUri") or (parsed_es.get("eliUri") if parsed_es else ""),
            "ca": {
                "url": parsed_ca.get("url"),
                "title": parsed_ca.get("title"),
                "formats": parsed_ca.get("formats"),
                "metadata": parsed_ca.get("metadata"),
                "additionalText": parsed_ca.get("additionalText"),
                "affectations": parsed_ca.get("affectations"),
                "descriptors": parsed_ca.get("descriptors"),
                "sections": parsed_ca.get("sections"),
                "attachments": parsed_ca.get("attachments")
            },
            "es": {
                "url": parsed_es.get("url") if parsed_es else url_es,
                "title": parsed_es.get("title") if parsed_es else None,
                "formats": parsed_es.get("formats") if parsed_es else None,
                "metadata": parsed_es.get("metadata") if parsed_es else None,
                "additionalText": parsed_es.get("additionalText") if parsed_es else None,
                "affectations": parsed_es.get("affectations") if parsed_es else None,
                "descriptors": parsed_es.get("descriptors") if parsed_es else None,
                "sections": parsed_es.get("sections") if parsed_es else None,
                "attachments": parsed_es.get("attachments") if parsed_es else None
            } if parsed_es else None
        }
        
        with open(output_filepath, "w", encoding="utf-8") as out:
            json.dump(bilingual_data, out, indent=2, ensure_ascii=False)
            
        return True
    except Exception:
        return False

def main():
    parser = argparse.ArgumentParser(description="Batch parse recent documents from XML (Akoma Ntoso) formats")
    parser.add_argument("--years", type=str, default="2024,2025,2026", help="Comma-separated list of years to parse (default: 2024,2025,2026)")
    parser.add_argument("--workers", type=int, default=20, help="Number of concurrent thread workers (default: 20)")
    parser.add_argument("--limit", type=int, default=None, help="Maximum number of documents to parse for testing")
    parser.add_argument("--no-resume", action="store_true", help="Overwrite existing parsed JSONs and XMLs")
    args = parser.parse_args()

    # Define paths relative to the Catalonia root
    cat_root = os.path.dirname(os.path.abspath(__file__))
    data_dir = os.path.join(cat_root, "data")
    
    dogc_json_path = os.path.join(data_dir, "dogc_documents.json")
    xml_dir = os.path.join(data_dir, "xml_output")
    structured_dir = os.path.join(data_dir, "structured_output")
    
    os.makedirs(xml_dir, exist_ok=True)
    os.makedirs(structured_dir, exist_ok=True)

    if not os.path.exists(dogc_json_path):
        print(f"Error: Base document dataset '{dogc_json_path}' not found. Please run get_dogc_documents.py first.")
        sys.exit(1)

    print(f"Loading base documents from {dogc_json_path}...")
    with open(dogc_json_path, "r", encoding="utf-8") as f:
        docs = json.load(f)

    year_list = [y.strip() for y in args.years.split(",") if y.strip()]
    target_docs = [d for d in docs if str(d.get("year") or "") in year_list]
    print(f"Selected {len(target_docs)} documents matching years: {year_list}")
    
    if args.limit:
        target_docs = target_docs[:args.limit]
        print(f"Limit applied. Processing {len(target_docs)} documents.")

    # Filter processed documents
    to_process = []
    skipped_count = 0
    for doc in target_docs:
        doc_id = doc.get("documentId")
        if not doc_id:
            continue
        output_filepath = os.path.join(structured_dir, f"dogc_doc_{doc_id}_structured.json")
        xml_path_ca = os.path.join(xml_dir, f"dogc_doc_{doc_id}_ca.xml")
        
        if not args.no_resume and os.path.exists(output_filepath) and os.path.exists(xml_path_ca):
            skipped_count += 1
        else:
            to_process.append(doc)
            
    print(f"Skipping {skipped_count} already processed documents. {len(to_process)} left to process.")

    if not to_process:
        print("Nothing to process.")
        sys.exit(0)

    print(f"Starting parallel download and parsing using {args.workers} workers...")
    success_count = 0
    failed_count = 0
    
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(process_single_doc_xml, doc, xml_dir, structured_dir): doc for doc in to_process}
        
        for future in tqdm(as_completed(futures), total=len(futures), desc="Processing XMLs"):
            try:
                success = future.result()
                if success:
                    success_count += 1
                else:
                    failed_count += 1
            except Exception:
                failed_count += 1
                
    print(f"\nProcessing finished. Success: {success_count}, Failed: {failed_count}.")

if __name__ == "__main__":
    main()
