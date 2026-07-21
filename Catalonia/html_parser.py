import os
import re
import sys
import json
import time
import argparse
import requests
from urllib.parse import urlparse, parse_qs, urljoin
from bs4 import BeautifulSoup, Comment
from playwright.sync_api import sync_playwright
from requests.adapters import HTTPAdapter
from urllib3.util import create_urllib3_context

# Custom SSL Adapter for legacy TLS handshakes on portaldogc.gencat.cat
class CustomSSLAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        context = create_urllib3_context()
        context.set_ciphers('DEFAULT@SECLEVEL=1')
        kwargs['ssl_context'] = context
        return super(CustomSSLAdapter, self).init_poolmanager(*args, **kwargs)

# Session for REST API calls
api_session = requests.Session()
api_session.mount('https://', CustomSSLAdapter())

# Lists of ordinal numbers in Catalan and Spanish to construct robust regex patterns
_CAT_ORDINALS = [
    "primer", "segon", "tercer", "quart", "cinquè", "sisè", "setè", "vuitè", "novè", "desè",
    "onzè", "dotzè", "tretzè", "catorzè", "quinzè", "setzè", "dissetè", "divuitè", "dinovè", "vintè",
    "vint-i-unè", "vint-i-dosè", "vint-i-tresè", "vint-i-quart", "vint-i-quatrenè", "vint-i-cinquè",
    "vint-i-sisè", "vint-i-setè", "vint-i-vuitè", "vint-i-novè", "trentè", "trenta-unè", "trenta-dosè",
    "trenta-tresè", "trenta-quart", "trenta-cinquè", "trenta-sisè", "trenta-setè", "trenta-vuitè",
    "trenta-novè", "quarantè", "quaranta-unè", "quaranta-dosè", "quaranta-tresè", "quaranta-quart",
    "quaranta-cinquè", "quaranta-sisè", "quaranta-setè", "quaranta-vuitè", "quaranta-novè", "cinquantè"
]

_ES_ORDINALS = [
    "primero", "primer", "segundo", "tercero", "tercer", "cuarto", "quinto", "sexto", "séptimo", "sétimo",
    "octavo", "noveno", "nono", "décimo", "undécimo", "decimoprimero", "decimoprimer", r"décimo\s+primero",
    r"décimo\s+primer", "duodécimo", "decimosegundo", r"décimo\s+segundo", "decimotercero", "decimotercer",
    r"décimo\s+tercero", r"décimo\s+tercer", "decimocuarto", r"décimo\s+cuarto", "decimoquinto",
    r"décimo\s+quinto", "decimosexto", r"décimo\s+sexto", "decimoséptimo", r"décimo\s+séptimo",
    "decimosétimo", r"décimo\s+sétimo", "decimooctavo", r"décimo\s+octavo", "decimonoveno", r"décimo\s+noveno",
    "decimonono", r"décimo\s+nono", "vigésimo", r"vigésimo\s+primero", r"vigésimo\s+primer", "vigesimoprimero",
    "vigesimoprimer", r"vigésimo\s+segundo", "vigesimosegundo", r"vigésimo\s+tercero", r"vigésimo\s+tercer",
    "vigesimotercero", "vigesimotercer", r"vigésimo\s+cuarto", "vigesimocuarto", r"vigésimo\s+quinto",
    "vigesimoquinto", r"vigésimo\s+sexto", "vigesimosexto", r"vigésimo\s+séptimo", "vigesimoséptimo",
    r"vigésimo\s+octavo", "vigesimooctavo", r"vigésimo\s+noveno", "vigesimonoveno", "trigésimo",
    r"trigésimo\s+primero", r"trigésimo\s+primer", "trigesimoprimero", "trigesimoprimer", r"trigésimo\s+segundo",
    "trigesimosegundo", r"trigésimo\s+tercero", r"trigésimo\s+tercer", "trigesimotercero", "trigesimotercer",
    r"trigésimo\s+cuarto", "trigesimocuarto", r"trigésimo\s+quinto", "trigesimoquinto", r"trigésimo\s+sexto",
    "trigesimosexto", r"trigésimo\s+séptimo", "trigesimoséptimo", r"trigésimo\s+octavo", "trigesimooctavo",
    r"trigésimo\s+noveno", "trigesimonoveno", "cuadragésimo", r"cuadragésimo\s+primero", r"cuadragésimo\s+primer",
    "cuadragesimoprimero", "cuadragesimoprimer", r"cuadragésimo\s+segundo", "cuadragesimosegundo",
    r"cuadragésimo\s+tercero", r"cuadragésimo\s+tercer", "cuadragesimotercero", "cuadragesimotercer",
    r"cuadragésimo\s+cuarto", "cuadragesimocuarto", r"cuadragésimo\s+quinto", "cuadragesimoquinto",
    r"cuadragésimo\s+sexto", "cuadragesimosexto", r"cuadragésimo\s+séptimo", "cuadragesimoséptimo",
    r"cuadragésimo\s+octavo", "cuadragesimooctavo", r"cuadragésimo\s+noveno", "cuadragesimonoveno",
    "quincuagésimo"
]

# Sort by length descending to ensure longer multi-word ordinals match first in alternation
_ALL_ORDINALS_SORTED = sorted(list(set(_CAT_ORDINALS + _ES_ORDINALS)), key=len, reverse=True)
_ORDINALS_PATTERN_STR = "|".join(_ALL_ORDINALS_SORTED)

CHAPTER_PAT = re.compile(
    r'^\s*(Capítol|Capítulo|Títol|Título|Secció|Sección)\s+(preliminar|[I|V|X|L|C]+|\d+[\w\-]*)\.?\s*(.*)',
    re.IGNORECASE
)

ARTICLE_PAT = re.compile(
    r'^\s*(Article|Artículo|Art\.)\s+(únic|único|' + _ORDINALS_PATTERN_STR + r'|\d+[\w\-]*)\.?\s*(.*)', 
    re.IGNORECASE
)

DISPOSITION_PAT = re.compile(
    r'^\s*(Disposició|Disposición)\s+(adicional|addicional|transitòria|transitoria|derogatòria|derogatoria|final)\s*(\w*)\.?\s*(.*)',
    re.IGNORECASE
)

ANNEX_PAT = re.compile(
    r'^\s*(Annex|Anexo)\s*(\d*|\b[I|V|X|L|C]+\b)?\.?\s*(.*)',
    re.IGNORECASE
)

RESOL_POINT_PAT = re.compile(
    r'^\s*(' + _ORDINALS_PATTERN_STR + r')\s*[\.\-:]\s*(.*)',
    re.IGNORECASE
)

# Start of signature block detection (standard Catalan law date/place header)
SIGNATURE_START_PAT = re.compile(
    r'^\s*(Barcelona|Palau de la Generalitat|Palacio de la Generalidad|Palacio de la Generalitat|Madrid|Girona|Lleida|Tarragona)\b.*,\s*[^0-9]*\d+\s+(?:de\s+|d\')?\w+\s+de(?:l)?\s+\d{4}',
    re.IGNORECASE
)

# Mapping Catalan metadata fields to standard camelCase JSON keys
KEY_MAPPING = {
    "Tipus de document": "typeOfLaw",
    "Data del document": "documentDate",
    "Número del document": "documentNumber",
    "Número de control": "controlNumber",
    "Organisme emissor": "emittingOrganism",
    "CVE": "cve",
    "Número del DOGC": "dogcNumber",
    "Data del DOGC": "dogcDate",
    "Secció del DOGC": "dogcSection"
}

def to_camel_case(s):
    s = re.sub(r'[^a-zA-Z0-9\s]', '', s)
    words = s.lower().split()
    if not words:
        return ""
    return words[0] + "".join(word.capitalize() for word in words[1:])

def table_to_markdown(table_el):
    markdown_lines = []
    rows = table_el.find_all('tr')
    if not rows:
        return ""
        
    max_cols = 0
    parsed_rows = []
    for row in rows:
        cells = row.find_all(['td', 'th'])
        max_cols = max(max_cols, len(cells))
        parsed_rows.append([cell.get_text(strip=True).replace('\n', ' ') for cell in cells])
        
    if max_cols == 0:
        return ""
        
    for i, row in enumerate(parsed_rows):
        while len(row) < max_cols:
            row.append("")
        markdown_lines.append("| " + " | ".join(row) + " |")
        if i == 0:
            separator = "| " + " | ".join(["---"] * max_cols) + " |"
            markdown_lines.append(separator)
            
    return "\n".join(markdown_lines)

def extract_doc_id_from_url(url):
    """
    If the URL is a direct DOGC link, extract the documentId from the query string.
    """
    parsed = urlparse(url)
    query_params = parse_qs(parsed.query)
    doc_id = query_params.get('documentId', [None])[0]
    return doc_id

def parse_image(img_el):
    src = img_el.get('src', '')
    alt = img_el.get('alt', '')
    title = img_el.get('title', '')
    return {
        "src": src,
        "alt": alt,
        "title": title
    }

def is_leaf_block(el):
    block_tags = {'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'table', 'img', 'pre', 'li', 'blockquote'}
    if el.name == 'table' or el.name == 'img':
        return True
    if el.name in block_tags:
        has_sub_blocks = any(sub.name in block_tags for sub in el.find_all() if sub != el)
        if not has_sub_blocks:
            return True
    if el.name == 'div':
        has_sub_blocks = any(sub.name in block_tags or sub.name == 'div' for sub in el.find_all() if sub != el)
        if not has_sub_blocks:
            return True
    return False

def build_affectation_text(aff):
    """
    Assembles a descriptive text string from EADOP API affectations response.
    """
    type_aff = aff.get("type_affectation") or ""
    conj_aff = aff.get("conj_affectation") or ""
    doc_title = aff.get("description_document", {}).get("title") or ""
    
    desc_val = aff.get("description_affect") or ""
    if isinstance(desc_val, str):
        desc_str = desc_val.strip()
    else:
        # It's a list (active affectation structure)
        parts = []
        for desc in desc_val:
            desc_type = desc.get("description") or ""
            titles = [item.get("title", "").strip() for item in desc.get("list") or [] if item.get("title")]
            if titles:
                parts.append(f"{desc_type} " + " ".join(titles))
        desc_str = " ".join(parts)
        
    if desc_str:
        text = f"{type_aff} {desc_str} {conj_aff} {doc_title}"
    else:
        text = f"{type_aff} {conj_aff} {doc_title}"
        
    return re.sub(r'\s+', ' ', text).strip()

def parse_document(html_content, url):
    soup = BeautifulSoup(html_content, 'html.parser')
    
    # 1. Document ID
    doc_id_input = soup.find('input', {'id': 'documentIdRequest'})
    doc_id = doc_id_input.get('value') if doc_id_input else "Unknown"
    
    # 2. ELI URI
    full_text_el = soup.find(id="fullText")
    if not full_text_el:
        return None  # No document body found
        
    # Preprocess <pre> tags by splitting into <p> tags on blank lines (paragraph breaks)
    pre_tags = full_text_el.find_all('pre')
    for pre in pre_tags:
        paragraphs = re.split(r'\n\s*\n', pre.get_text())
        new_elements = []
        for p_text in paragraphs:
            cleaned = re.sub(r'\s+', ' ', p_text).strip()
            if cleaned:
                new_p = soup.new_tag('p')
                new_p.string = cleaned
                new_elements.append(new_p)
        for el in new_elements:
            pre.insert_before(el)
        pre.decompose()
        
    eli_el = full_text_el.find(class_='uriEli') or full_text_el.find(id='uriEli')
    eli_uri = ""
    if eli_el:
        eli_uri = eli_el.get_text().replace("URI ELI:", "").strip()
        
    # 3. Document Title
    title_el = full_text_el.find('h1')
    title = title_el.get_text().strip() if title_el else "Unknown Title"
    title = re.sub(r'\s+', ' ', title)
    
    # 3.5. Additional text notices (e.g. "Este documento está disponible en formato PDF")
    additional_text = None
    alert_el = soup.find(class_='alert') or soup.find(class_='avis') or soup.find(class_='alerta') or soup.find(id='docDispUnicPdf')
    if alert_el:
        if alert_el.name == 'input':
            additional_text = alert_el.get('value', '').strip()
        else:
            additional_text = alert_el.get_text().strip()
            additional_text = re.sub(r'\s+', ' ', additional_text)
    
    # 4. Metadata details
    metadata = {}
    meta_block = soup.find('ul', id='disposicions_cos_bloc')
    if meta_block:
        for li in meta_block.find_all('li', recursive=False):
            text = li.get_text(separator='\n').strip()
            lines = [line.strip() for line in text.split('\n') if line.strip()]
            if len(lines) >= 2:
                key = lines[0]
                val = " ".join(lines[1:])
                json_key = KEY_MAPPING.get(key, to_camel_case(key))
                metadata[json_key] = val
                
    # 5. Affectations
    affectations = {"passive": [], "active": []}
    passive_ul = soup.find('ul', id='affectations_passive_ul')
    if passive_ul:
        for li in passive_ul.find_all('li', recursive=False):
            text = re.sub(r'\s+', ' ', li.get_text().strip())
            a_tag = li.find('a')
            target_id = None
            if a_tag and 'documentId=' in a_tag.get('href', ''):
                match = re.search(r'documentId=(\d+)', a_tag.get('href'))
                if match:
                    target_id = match.group(1)
            affectations["passive"].append({"text": text, "targetDocumentId": target_id})
            
    active_ul = soup.find('ul', id='affectations_active_ul')
    if active_ul:
        for li in active_ul.find_all('li', recursive=False):
            text = re.sub(r'\s+', ' ', li.get_text().strip())
            a_tag = li.find('a')
            target_id = None
            if a_tag and 'documentId=' in a_tag.get('href', ''):
                match = re.search(r'documentId=(\d+)', a_tag.get('href'))
                if match:
                    target_id = match.group(1)
            affectations["active"].append({"text": text, "targetDocumentId": target_id})
            
    # 6. Descriptors
    descriptors = {"organisms": [], "geographic": [], "thematic": []}
    
    org_block = soup.find('ul', id='related_body_block-organizationDescriptor')
    if org_block:
        for a in org_block.find_all('a'):
            descriptors["organisms"].append({"id": a.get('id'), "name": a.get('title') or a.get_text().strip()})
            
    geo_block = soup.find('ul', id='related_body_block-geographicDescriptor')
    if geo_block:
        for a in geo_block.find_all('a'):
            descriptors["geographic"].append({"id": a.get('id'), "name": a.get('title') or a.get_text().strip()})
            
    them_block = soup.find('ul', id='related_body_block-thematicDescriptor')
    if them_block:
        for a in them_block.find_all('a'):
            descriptors["thematic"].append({"id": a.get('id'), "name": a.get('title') or a.get_text().strip()})
            
    # 6.5. Formats
    formats = {}
    download_li = soup.find('li', id='download')
    if download_li:
        for a in download_li.find_all('a'):
            href = a.get('href')
            if href:
                abs_href = urljoin(url, href)
                classes = a.get('class') or []
                title_attr = (a.get('title') or '').lower()
                text_content = (a.get_text() or '').strip().lower()
                
                if 'pdf' in classes or 'pdf' in title_attr or 'pdf' in text_content or 'PdfProviderServlet' in href:
                    formats['pdf'] = abs_href
                elif 'rdf' in classes or 'rdf' in title_attr or 'rdf' in text_content or 'format=rdf' in href:
                    formats['rdf'] = abs_href
                elif 'ttl' in classes or 'ttl' in title_attr or 'ttl' in text_content or 'turtle' in title_attr or 'format=turtle' in href:
                    formats['ttl'] = abs_href
                elif 'xml' in classes or 'xml' in title_attr or 'xml' in text_content or 'format=xml' in href:
                    formats['xml'] = abs_href
    else:
        # Fallback if no #download element
        for a in soup.find_all('a'):
            href = a.get('href')
            if href:
                abs_href = urljoin(url, href)
                classes = a.get('class') or []
                title_attr = (a.get('title') or '').lower()
                text_content = (a.get_text() or '').strip().lower()
                
                if 'pdf' in classes or 'PdfProviderServlet' in href:
                    formats['pdf'] = abs_href
                elif 'rdf' in classes or 'format=rdf' in href:
                    formats['rdf'] = abs_href
                elif 'ttl' in classes or 'format=turtle' in href:
                    formats['ttl'] = abs_href
                elif 'xml' in classes or 'format=xml' in href:
                    formats['xml'] = abs_href

    # Preprocess full_text_el to wrap bare text nodes in <p>
    block_tags = {'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'table', 'img', 'pre', 'li', 'blockquote'}
    containers = [full_text_el] + full_text_el.find_all(lambda tag: tag.name in ['div', 'section'])
    for container in containers:
        has_block = any(child.name in block_tags or child.name == 'div' for child in container.children)
        if has_block:
            for child in list(container.children):
                if child.name is None:
                    if isinstance(child, Comment):
                        continue
                    text = child.strip()
                    if text:
                        new_p = soup.new_tag("p")
                        new_p.string = text
                        child.replace_with(new_p)
                        
    # Build block elements list, skipping title_el and eli_el
    block_elements = []
    for el in full_text_el.find_all(recursive=True):
        if is_leaf_block(el):
            if el == title_el or el == eli_el:
                continue
            if title_el and title_el in el.parents:
                continue
            if eli_el and eli_el in el.parents:
                continue
            if el.name in ['table', 'img'] or el.get_text(strip=True):
                block_elements.append(el)
                
    # 7. Traverse blocks and parse layout structure (Preamble, Articles, Signatures)
    sections = []
    attachments = []
    attachment_counter = 0
    current_chapter = None
    
    current_section = {
        "type": "introduction",
        "title": "Preamble",
        "heading": None,
        "chapter": None,
        "commas": [],
        "attachments": []
    }
    sections.append(current_section)
    
    idx = 0
    n_blocks = len(block_elements)
    
    while idx < n_blocks:
        el = block_elements[idx]
        
        if el.name == 'table':
            attachment_counter += 1
            att_id = f"att_{attachment_counter}"
            md_table = table_to_markdown(el)
            att_obj = {
                "id": att_id,
                "type": "table",
                "content": md_table,
                "sectionTitle": current_section["title"]
            }
            attachments.append(att_obj)
            current_section["commas"].append(f"[ATTACHMENT: {att_id}]")
            current_section["attachments"].append(att_obj)
            idx += 1
            
        elif el.name == 'img':
            attachment_counter += 1
            att_id = f"att_{attachment_counter}"
            img_info = parse_image(el)
            att_obj = {
                "id": att_id,
                "type": "image",
                "content": img_info,
                "sectionTitle": current_section["title"]
            }
            attachments.append(att_obj)
            current_section["commas"].append(f"[ATTACHMENT: {att_id}]")
            current_section["attachments"].append(att_obj)
            idx += 1
            
        else:
            text = el.get_text().strip()
            text = re.sub(r'\s+', ' ', text)
            
            # Detect Chapters
            m_chap = CHAPTER_PAT.match(text)
            # Detect standard Articles
            m_art = ARTICLE_PAT.match(text)
            # Detect Resolution points (e.g. "Primer. ...")
            m_resol = RESOL_POINT_PAT.match(text)
            # Detect Dispositions
            m_disp = DISPOSITION_PAT.match(text)
            # Detect Annexes
            m_annex = ANNEX_PAT.match(text)
            
            if m_chap:
                chap_type = m_chap.group(1).capitalize()
                chap_num = m_chap.group(2)
                heading = m_chap.group(3)
                
                chap_title = f"{chap_type} {chap_num}"
                current_chapter = f"{chap_title}. {heading}".strip(". ") if heading else chap_title
                
                current_section = {
                    "type": "chapter",
                    "title": chap_title,
                    "heading": heading or None,
                    "chapter": current_chapter,
                    "commas": [],
                    "attachments": []
                }
                sections.append(current_section)
                idx += 1

            elif m_art:
                art_num = m_art.group(2)
                heading = m_art.group(3)
                
                # Check if next block is a heading desc or if it's the start of article body
                if not heading and idx + 1 < n_blocks:
                    next_el = block_elements[idx+1]
                    next_text = next_el.get_text().strip()
                    if next_el.name not in ['table', 'img'] and not CHAPTER_PAT.match(next_text) and not ARTICLE_PAT.match(next_text) and not DISPOSITION_PAT.match(next_text) and not ANNEX_PAT.match(next_text) and not SIGNATURE_START_PAT.match(next_text) and not RESOL_POINT_PAT.match(next_text):
                        heading = re.sub(r'\s+', ' ', next_text)
                        idx += 1
                        
                current_section = {
                    "type": "article",
                    "title": f"Article {art_num}",
                    "heading": heading or None,
                    "chapter": current_chapter,
                    "commas": [],
                    "attachments": []
                }
                sections.append(current_section)
                idx += 1
                
            elif m_resol:
                pt_name = m_resol.group(1).capitalize()
                heading = m_resol.group(2)
                
                current_section = {
                    "type": "article", # Map resolution points as article nodes
                    "title": pt_name,
                    "heading": heading or None,
                    "chapter": current_chapter,
                    "commas": [],
                    "attachments": []
                }
                sections.append(current_section)
                idx += 1
                
            elif m_disp:
                disp_type = m_disp.group(2).capitalize()
                disp_num = m_disp.group(3)
                heading = m_disp.group(4)
                
                disp_title = f"Disposició {disp_type}"
                if disp_num:
                    disp_title += f" {disp_num}"
                    
                current_section = {
                    "type": "disposition",
                    "title": disp_title,
                    "heading": heading or None,
                    "chapter": current_chapter,
                    "commas": [],
                    "attachments": []
                }
                sections.append(current_section)
                idx += 1
                
            elif m_annex:
                annex_num = m_annex.group(2) or ""
                heading = m_annex.group(3)
                
                annex_title = "Annex"
                if annex_num:
                    annex_title += f" {annex_num}"
                    
                current_section = {
                    "type": "annex",
                    "title": annex_title,
                    "heading": heading or None,
                    "chapter": current_chapter,
                    "commas": [],
                    "attachments": []
                }
                sections.append(current_section)
                idx += 1
                
            elif SIGNATURE_START_PAT.match(text):
                # Start signature block section
                current_section = {
                    "type": "signature",
                    "title": "Signatures",
                    "heading": None,
                    "chapter": current_chapter,
                    "commas": [text],
                    "attachments": []
                }
                sections.append(current_section)
                idx += 1
                
                # Consume all remaining blocks into signature commas, unless they start a new section
                while idx < n_blocks:
                    rem_el = block_elements[idx]
                    
                    # Check if the block starts a new section (Annex, Article, Disposition, or another Signature block)
                    if rem_el.name not in ['table', 'img']:
                        rem_text = re.sub(r'\s+', ' ', rem_el.get_text().strip())
                        if ANNEX_PAT.match(rem_text) or ARTICLE_PAT.match(rem_text) or DISPOSITION_PAT.match(rem_text) or RESOL_POINT_PAT.match(rem_text) or SIGNATURE_START_PAT.match(rem_text):
                            break
                            
                    if rem_el.name == 'table':
                        attachment_counter += 1
                        att_id = f"att_{attachment_counter}"
                        md_table = table_to_markdown(rem_el)
                        att_obj = {
                            "id": att_id,
                            "type": "table",
                            "content": md_table,
                            "sectionTitle": "Signatures"
                        }
                        attachments.append(att_obj)
                        current_section["commas"].append(f"[ATTACHMENT: {att_id}]")
                        current_section["attachments"].append(att_obj)
                    elif rem_el.name == 'img':
                        attachment_counter += 1
                        att_id = f"att_{attachment_counter}"
                        img_info = parse_image(rem_el)
                        att_obj = {
                            "id": att_id,
                            "type": "image",
                            "content": img_info,
                            "sectionTitle": "Signatures"
                        }
                        attachments.append(att_obj)
                        current_section["commas"].append(f"[ATTACHMENT: {att_id}]")
                        current_section["attachments"].append(att_obj)
                    else:
                        rem_text = re.sub(r'\s+', ' ', rem_el.get_text().strip())
                        if rem_text:
                            current_section["commas"].append(rem_text)
                    idx += 1
            else:
                # Add text block as a comma inside the current section
                current_section["commas"].append(text)
                idx += 1
                
    # Clean empty introduction if we immediately started with articles
    if len(sections) > 1 and sections[0]["type"] == "introduction" and not sections[0]["commas"] and not sections[0]["attachments"]:
        sections.pop(0)
        
    return {
        "documentId": doc_id,
        "url": url,
        "eliUri": eli_uri,
        "title": title,
        "formats": formats,
        "metadata": metadata,
        "additionalText": additional_text,
        "affectations": affectations,
        "descriptors": descriptors,
        "sections": sections,
        "attachments": attachments
    }

def fetch_document_from_rest_api(doc_id, url, language="ca"):
    """
    Directly queries EADOP REST API endpoints to build the structured document.
    Bypasses Akamai WAF blocks entirely.
    """
    try:
        # 1. Fetch main document text & data
        r_doc = api_session.post("https://portaldogc.gencat.cat/eadop-rest/api/dogc/documentDOGC", 
                                 data={"documentId": str(doc_id), "language": language}, timeout=15)
        if r_doc.status_code != 200:
            return None
        res_doc = r_doc.json()
        doc_data = res_doc.get("documentData") or {}
        
        # 2. Fetch affectations
        r_aff = api_session.post("https://portaldogc.gencat.cat/eadop-rest/api/dogc/getDocumentAffectations", 
                                 data={"documentId": str(doc_id), "language": language}, timeout=15)
        affectations = {"passive": [], "active": []}
        if r_aff.status_code == 200:
            res_aff_root = r_aff.json() or {}
            res_aff = res_aff_root.get("affectations") or {}
            
            # Map passive affectations
            passives = res_aff.get("passiveAffectations", {}).get("affectationList") or []
            for aff in passives:
                text = build_affectation_text(aff)
                target_doc = aff.get("description_document") or {}
                affectations["passive"].append({
                    "text": text,
                    "targetDocumentId": str(target_doc.get("documentId")) if target_doc.get("documentId") else None
                })
                
            # Map active affectations
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
                
        # 4. Construct mock HTML to reuse BS4 text segmenter
        text_html = res_doc.get("textDocument") or ""
        title_doc = res_doc.get("titleDocument") or "Unknown Title"
        eli_val = res_doc.get("uriELI", {}).get("link") if res_doc.get("uriELI") else ""
        
        mock_html = f"""
        <html>
          <body>
            <input type="hidden" id="documentIdRequest" value="{doc_id}">
            <div id="fullText">
              <span id="uriEli">URI ELI: {eli_val}</span>
              <h1>{title_doc}</h1>
              {text_html}
            </div>
          </body>
        </html>
        """
        
        # 5. Segment clauses using BS4
        parsed = parse_document(mock_html, url)
        if not parsed:
            return None
            
        # 6. Override parsed metadata, affectations, descriptors, and formats directly from API structures
        parsed["eliUri"] = eli_val
        parsed["affectations"] = affectations
        parsed["descriptors"] = descriptors
        
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
        
        # Override additionalText from API response
        text_adicional_obj = res_doc.get("textAdicional") or {}
        parsed["additionalText"] = text_adicional_obj.get("text")
        
        date_doc = doc_data.get("dateDocument")
        year = date_doc.split("/")[-1] if date_doc and len(date_doc) >= 10 else doc_data.get("year") or ""
        
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
    except Exception as e:
        print(f"Error fetching from REST API for doc_id {doc_id}: {e}", file=sys.stderr)
    return None

def get_spanish_url(url):
    """
    Translates a Catalonian document URL to its Spanish counterpart.
    - DOGC: /ca/ -> /es/
    - Portal Jurídic: /cat/ -> /spa/
    """
    if "/ca/" in url:
        return url.replace("/ca/", "/es/")
    elif "/cat/" in url:
        return url.replace("/cat/", "/spa/")
    return url

def main():
    parser = argparse.ArgumentParser(description="Structured Catalan Law Parser")
    parser.add_argument("--offset", type=int, default=0, help="Offset to start processing from")
    parser.add_argument("--limit", type=int, default=10, help="Limit number of URLs to process")
    parser.add_argument("--url", type=str, help="Process a single direct URL (ignores list files)")
    parser.add_argument("--all", action="store_true", help="Process all URLs in input file")
    args = parser.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    urls_filepath = os.path.join(script_dir, "law_urls.txt")
    metadata_filepath = os.path.join(script_dir, "law_metadata.json")
    if not os.path.exists(metadata_filepath):
        metadata_filepath = os.path.join(script_dir, "old_files", "law_metadata.json")
    
    # Load metadata list to resolve documentId mappings
    url_to_id = {}
    if os.path.exists(metadata_filepath):
        try:
            with open(metadata_filepath, "r", encoding="utf-8") as f:
                metadata_list = json.load(f)
                for item in metadata_list:
                    if item.get("url") and item.get("documentId"):
                        url_to_id[item["url"]] = str(item["documentId"])
                    if item.get("dogcUrl") and item.get("documentId"):
                        url_to_id[item["dogcUrl"]] = str(item["documentId"])
        except Exception as e:
            print(f"Warning: Failed to load law_metadata.json for URL mappings: {e}")

    urls_to_process = []
    if args.url:
        urls_to_process = [args.url]
    else:
        if not os.path.exists(urls_filepath):
            print(f"Error: URLs list file {urls_filepath} not found.")
            sys.exit(1)
            
        with open(urls_filepath, "r", encoding="utf-8") as f:
            law_urls = [line.strip() for line in f if line.strip()]
            
        # Apply offset and limit
        urls_to_process = law_urls[args.offset:]
        if not args.all:
            urls_to_process = urls_to_process[:args.limit]
            
    print(f"Processing {len(urls_to_process)} URLs...")
    
    output_dir = os.path.join(script_dir, "data", "structured_output")
    os.makedirs(output_dir, exist_ok=True)
    
    # Initialize Playwright lazily only if fallback is needed
    playwright_context = None
    playwright_browser = None
    playwright_instance = None
    
    def get_playwright():
        nonlocal playwright_context, playwright_browser, playwright_instance
        if playwright_context is None:
            playwright_instance = sync_playwright().start()
            playwright_browser = playwright_instance.chromium.launch(headless=True, args=["--lang=ca-ES"])
            playwright_context = playwright_browser.new_context(locale="ca-ES")
        return playwright_context

    success_count = 0
    failure_count = 0

    for url in urls_to_process:
        try:
            print(f"\nProcessing URL: {url} ...")
            parsed_ca = None
            parsed_es = None
            
            # Resolve documentId
            doc_id = extract_doc_id_from_url(url)
            if not doc_id and url in url_to_id:
                doc_id = url_to_id[url]
            
            url_ca = url
            url_es = get_spanish_url(url)
                
            # Method 1: Fetch via EADOP REST API (highly preferred, bypasses WAF)
            if doc_id:
                print(f"Attempting REST API query for documentId: {doc_id} (Catalan)...")
                parsed_ca = fetch_document_from_rest_api(doc_id, url_ca, language="ca")
                if parsed_ca:
                    print("--> [Success] Retrieved and parsed Catalan via EADOP REST API.")
                
                print(f"Attempting REST API query for documentId: {doc_id} (Spanish)...")
                try:
                    parsed_es = fetch_document_from_rest_api(doc_id, url_es, language="es")
                    if parsed_es:
                        print("--> [Success] Retrieved and parsed Spanish via EADOP REST API.")
                except Exception as e:
                    print(f"--> [Warning] Failed to fetch Spanish via EADOP REST API: {e}")
                    
            # Method 2: Playwright page crawler fallback
            if not parsed_ca:
                print("Fallback: Using Playwright browser crawler for Catalan...")
                try:
                    ctx = get_playwright()
                    page = ctx.new_page()
                    page.goto(url_ca)
                    
                    page.wait_for_selector("#fullText:not(:empty)", timeout=15000)
                    page.evaluate("""
                        jQuery('a[data-toggle="collapse"]').each(function() {
                            var $link = jQuery(this);
                            if ($link.is(':visible') && $link.hasClass('collapsed')) {
                                $link.click();
                            }
                        });
                    """)
                    page.wait_for_load_state("networkidle")
                    time.sleep(1)
                    
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
                    
                    html_content_ca = page.content()
                    page.close()
                    
                    parsed_ca = parse_document(html_content_ca, url_ca)
                    if parsed_ca:
                        print("--> [Success] Retrieved and parsed Catalan via Playwright fallback.")
                except Exception as e:
                    print(f"--> [Error] Playwright fallback failed for Catalan: {e}")
            
            if parsed_ca and not parsed_es:
                print("Fallback: Using Playwright browser crawler for Spanish...")
                try:
                    ctx = get_playwright()
                    page = ctx.new_page()
                    page.goto(url_es)
                    
                    page.wait_for_selector("#fullText:not(:empty)", timeout=15000)
                    page.evaluate("""
                        jQuery('a[data-toggle="collapse"]').each(function() {
                            var $link = jQuery(this);
                            if ($link.is(':visible') && $link.hasClass('collapsed')) {
                                $link.click();
                            }
                        });
                    """)
                    page.wait_for_load_state("networkidle")
                    time.sleep(1)
                    
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
                    
                    html_content_es = page.content()
                    page.close()
                    
                    parsed_es = parse_document(html_content_es, url_es)
                    if parsed_es:
                        print("--> [Success] Retrieved and parsed Spanish via Playwright fallback.")
                except Exception as e:
                    print(f"--> [Warning] Playwright fallback failed for Spanish: {e}")
                    
            if parsed_ca:
                # Merge into bilingual format
                bilingual_data = {
                    "documentId": doc_id or parsed_ca.get("documentId"),
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
                
                # Determine output filename
                parsed_url = urlparse(url)
                doc_id_param = doc_id or parsed_ca.get("documentId")
                
                if doc_id_param and doc_id_param != "Unknown":
                    domain_part = "dogc" if "dogc" in parsed_url.netloc else "pjur"
                    clean_filename = f"{domain_part}_doc_{doc_id_param}"
                else:
                    path_parts = [p for p in parsed_url.path.split('/') if p]
                    if 'eli' in path_parts:
                        eli_idx = path_parts.index('eli')
                        filename_parts = path_parts[eli_idx + 1:]
                    else:
                        filename_parts = path_parts
                    clean_filename = "_".join(filename_parts)
                    clean_filename = re.sub(r'[^a-zA-Z0-9_\-]', '', clean_filename)
                    
                output_filename = f"{clean_filename}_structured.json"
                output_filepath = os.path.join(output_dir, output_filename)
                
                with open(output_filepath, "w", encoding="utf-8") as out:
                    json.dump(bilingual_data, out, indent=2, ensure_ascii=False)
                print(f"Successfully saved structured bilingual JSON to {output_filepath}")
                success_count += 1
            else:
                print(f"--> [Failed] Could not parse Catalan document content.")
                failure_count += 1
                
        except Exception as e:
            print(f"--> [Error] Failed to process {url}: {e}")
            failure_count += 1

    # Cleanup Playwright if started
    if playwright_browser:
        playwright_browser.close()
    if playwright_instance:
        playwright_instance.stop()
        
    print(f"\nProcessing finished. Success: {success_count}, Failed: {failure_count}.")

if __name__ == "__main__":
    main()
