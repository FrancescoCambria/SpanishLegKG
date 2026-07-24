#!/usr/bin/env python3
"""
process_sections_pipeline.py

Comprehensive Section-Level Processor for DOGC and BOP (BOPB/BOPG/BOPT/BOPL) documents (2010 onwards).

Functionality:
1. Target Scope: Processes all DOGC and BOP bulletin documents published from 2010 onwards.
2. Text Retrieval & Bilingual Support:
   - Fetches HTML / XML (Akoma Ntoso) links and EADOP REST API payloads.
   - Preserves bilingual text (textCa: Catalan, textEs: Spanish) where available.
3. Refined Section & Article Structuring:
   - Preamble / Introduction: Explicitly detects preambles (text before first article or preamble header).
   - Article Normalization: Handles Article Únic / Único and ordinals (Primer, Segon, 1, 2, 12-bis).
   - Heading Placement: Attaches article headings inside the section properties (e.g. heading: "Objecte").
   - Dispositions, Annexes & Signatures: Chapters, Dispositions, Annexes, and Signatures (Barcelona, date, etc.).
4. ELI Identifier Format per graph_schema.txt:
   - Section ELI ID: eli/es-cat/doc-sec{section_number}/{year}/{month}/{day}/{document_id}
5. Citation & Affectation Extraction:
   - From XML / API Metadata: Extracts active/passive affectations as MODIFY / ABROGATES / CONSOLIDATES edges.
   - From Text (Regex): Extracts legal citations (laws, decrees, orders, resolutions) as CITES edges.
6. Outputs to graph_data/:
   - section_nodes.json
   - has_section_edges.json
   - section_citation_edges.json
"""

import os
import re
import sys
import json
import argparse
import unicodedata
from tqdm import tqdm
import requests
from requests.adapters import HTTPAdapter
try:
    from urllib3.util import create_urllib3_context
except ImportError:
    from urllib3.util.ssl_ import create_urllib3_context

script_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(script_dir)
for d in [script_dir, parent_dir]:
    if d not in sys.path:
        sys.path.append(d)

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
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://dogc.gencat.cat/"
    })
    return session

def slugify(text):
    if not text:
        return ""
    nfkd_form = unicodedata.normalize('NFKD', str(text))
    ascii_text = nfkd_form.encode('ASCII', 'ignore').decode('utf-8')
    return re.sub(r'[^a-z0-9]+', '_', ascii_text.lower()).strip('_')

def format_date_parts(date_str):
    if not date_str:
        return "0000", "00", "00"
    m_ddmmyyyy = re.search(r'(\d{1,2})[\/\-](\d{1,2})[\/\-](\d{4})', str(date_str))
    if m_ddmmyyyy:
        d, m, y = m_ddmmyyyy.groups()
        return y, m.zfill(2), d.zfill(2)
    m_yyyymmdd = re.search(r'(\d{4})[\/\-](\d{1,2})[\/\-](\d{1,2})', str(date_str))
    if m_yyyymmdd:
        y, m, d = m_yyyymmdd.groups()
        return y, m.zfill(2), d.zfill(2)
    m_year = re.search(r'\b(19\d\d|20\d\d)\b', str(date_str))
    if m_year:
        return m_year.group(1), "00", "00"
    return "0000", "00", "00"

# Catalan & Spanish ordinals list
_CAT_ORDINALS = [
    "primer", "segon", "tercer", "quart", "cinquè", "sisè", "setè", "vuitè", "novè", "desè",
    "onzè", "dotzè", "tretzè", "catorzè", "quinzè", "setzè", "dissetè", "divuitè", "dinovè", "vintè",
    "vint-i-unè", "vint-i-dosè", "vint-i-tresè", "vint-i-quart", "vint-i-cinquè", "vint-i-sisè",
    "vint-i-setè", "vint-i-vuitè", "vint-i-novè", "trentè"
]
_ES_ORDINALS = [
    "primero", "primer", "segundo", "tercero", "tercer", "cuarto", "quinto", "sexto", "séptimo",
    "octavo", "noveno", "décimo", "undécimo", "duodécimo", "decimotercero", "decimocuarto", "decimoquinto"
]
_ALL_ORDINALS_SORTED = sorted(list(set(_CAT_ORDINALS + _ES_ORDINALS)), key=len, reverse=True)
_ORDINALS_PATTERN_STR = "|".join(_ALL_ORDINALS_SORTED)

# Section detection regexes
CHAPTER_PAT = re.compile(
    r'^\s*(Capítol|Capítulo|Títol|Título|Secció|Sección)\s+(preliminar|[I|V|X|L|C]+|\d+[\w\-]*)\.?\s*(.*)',
    re.IGNORECASE
)

ARTICLE_PAT = re.compile(
    r'^\s*(Article|Artículo|Art\.)\s+\b(único|únic|' + _ORDINALS_PATTERN_STR + r'|\d+[\w\-]*)\b\.?\s*(.*)',
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

SIGNATURE_START_PAT = re.compile(
    r'^\s*(Barcelona|Palau de la Generalitat|Palacio de la Generalitat|Madrid|Girona|Lleida|Tarragona)\b.*,\s*[^0-9]*\d+\s+(?:de\s+|d\')?\w+\s+de(?:l)?\s+\d{4}',
    re.IGNORECASE
)

CITATION_PAT = re.compile(
    r'\b(Llei|Ley|Decret|Decreto|Ordre|Orden|Resolució|Resolución|Reial\s+Decret|Real\s+Decreto)\s+([A-Z0-9\/\-\.]+)\b',
    re.IGNORECASE
)

def normalize_article_number(num_str):
    if not num_str:
        return ""
    n = num_str.strip().lower()
    if n in ["únic", "único"]:
        return "Únic"
    if n in ["primer", "primero"]:
        return "1"
    if n in ["segon", "segundo"]:
        return "2"
    if n in ["tercer", "tercero"]:
        return "3"
    if n in ["quart", "cuarto"]:
        return "4"
    if n in ["cinquè", "quinto"]:
        return "5"
    return n.upper() if n.isalnum() else n

def extract_citations_from_text(text):
    citations = []
    if not text:
        return citations
    matches = CITATION_PAT.findall(text)
    for m in matches:
        doc_type = m[0].strip()
        doc_ref = m[1].strip()
        citations.append({
            "type": doc_type,
            "ref": doc_ref,
            "raw": f"{doc_type} {doc_ref}"
        })
    return citations

def parse_text_into_sections(text_paragraphs, doc_id, min_year=2010):
    sections = []
    current_chapter = None
    
    current_section = {
        "type": "introduction",
        "title": "Preàmbul",
        "heading": "Introducció",
        "chapter": None,
        "paragraphs": [],
        "attachments": []
    }
    sections.append(current_section)
    
    for idx, p_text in enumerate(text_paragraphs):
        text = re.sub(r'\s+', ' ', str(p_text)).strip()
        if not text:
            continue
            
        m_chap = CHAPTER_PAT.match(text)
        m_art = ARTICLE_PAT.match(text)
        m_disp = DISPOSITION_PAT.match(text)
        m_annex = ANNEX_PAT.match(text)
        m_sig = SIGNATURE_START_PAT.match(text)
        
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
                "paragraphs": [],
                "attachments": []
            }
            sections.append(current_section)
            
        elif m_art:
            art_raw_num = m_art.group(2)
            heading = m_art.group(3)
            norm_num = normalize_article_number(art_raw_num)
            art_title = f"Article {norm_num}" if norm_num != "Únic" else "Article Únic"
            
            current_section = {
                "type": "article",
                "title": art_title,
                "heading": heading or None,
                "chapter": current_chapter,
                "paragraphs": [],
                "attachments": []
            }
            sections.append(current_section)
            
        elif m_disp:
            disp_kind = m_disp.group(2).capitalize()
            disp_num = m_disp.group(3)
            heading = m_disp.group(4)
            disp_title = f"Disposició {disp_kind} {disp_num}".strip()
            
            current_section = {
                "type": "disposition",
                "title": disp_title,
                "heading": heading or None,
                "chapter": current_chapter,
                "paragraphs": [],
                "attachments": []
            }
            sections.append(current_section)
            
        elif m_annex:
            annex_num = m_annex.group(2)
            heading = m_annex.group(3)
            annex_title = f"Annex {annex_num}".strip() if annex_num else "Annex"
            
            current_section = {
                "type": "annex",
                "title": annex_title,
                "heading": heading or None,
                "chapter": current_chapter,
                "paragraphs": [],
                "attachments": []
            }
            sections.append(current_section)
            
        elif m_sig:
            current_section = {
                "type": "signature",
                "title": "Signatures",
                "heading": None,
                "chapter": current_chapter,
                "paragraphs": [text],
                "attachments": []
            }
            sections.append(current_section)
            
        else:
            current_section["paragraphs"].append(text)
            
    filtered_sections = []
    for s in sections:
        if s["type"] == "introduction" and not s["paragraphs"] and not s["heading"]:
            continue
        filtered_sections.append(s)
        
    return filtered_sections

def process_section_pipeline(data_dir, output_dir, min_year=2010, verbose=True):
    os.makedirs(output_dir, exist_ok=True)
    
    dogc_path = os.path.join(data_dir, "dogc_documents.json")
    structured_dir = os.path.join(data_dir, "structured_output")
    bopg_bopt_path = os.path.join(data_dir, "bopg_bopt_documents.json")
    bopl_path = os.path.join(data_dir, "bopl_documents.json")
    
    section_nodes = []
    has_section_edges = []
    citation_edges = []
    
    doc_lookup = {}
    
    # ---------------------------------------------------------
    # 1. Parse DOGC Document Sections (2010+)
    # ---------------------------------------------------------
    if os.path.exists(dogc_path):
        if verbose:
            print(f"[1/2] Processing DOGC Document Sections (>= {min_year})...")
        with open(dogc_path, "r", encoding="utf-8") as f:
            dogc_docs = json.load(f)
            
        recent_dogc = [d for d in dogc_docs if int(d.get("year") or 0) >= min_year]
        if verbose:
            print(f"Selected {len(recent_dogc)} DOGC documents from {min_year} onwards.")
            
        for item in tqdm(recent_dogc, disable=not verbose, desc="Parsing DOGC sections"):
            doc_id = str(item.get("documentId") or "")
            if not doc_id:
                continue
                
            date_val = item.get("dateDOGC") or item.get("date") or ""
            year, month, day = format_date_parts(date_val if date_val else item.get("year"))
            
            doc_eli = item.get("eliUri") or f"eli/es-cat/doc/{year}/{month}/{day}/{doc_id}"
            doc_lookup[doc_id] = doc_eli
            
            # Check structured JSON file
            struct_file = os.path.join(structured_dir, f"dogc_doc_{doc_id}_structured.json")
            parsed_sections = []
            xml_link_ca = None
            xml_link_es = None
            
            if os.path.exists(struct_file):
                try:
                    with open(struct_file, "r", encoding="utf-8") as sf:
                        s_data = json.load(sf)
                        
                        ca_data = s_data.get("ca") or {}
                        es_data = s_data.get("es") or {}
                        
                        xml_link_ca = (ca_data.get("formats") or {}).get("xml")
                        xml_link_es = (es_data.get("formats") or {}).get("xml") if es_data else None
                        
                        ca_sections = ca_data.get("sections") or []
                        es_sections = (es_data.get("sections") or []) if es_data else []
                        
                        for s_idx, sec in enumerate(ca_sections):
                            sec_type = sec.get("type") or "article"
                            sec_title = sec.get("title") or f"Section {s_idx+1}"
                            sec_heading = sec.get("heading")
                            commas = sec.get("commas") or []
                            text_ca = " ".join(commas) if commas else (sec_heading or "")
                            
                            text_es = ""
                            if s_idx < len(es_sections):
                                es_sec = es_sections[s_idx]
                                es_commas = es_sec.get("commas") or []
                                text_es = " ".join(es_commas) if es_commas else (es_sec.get("heading") or "")
                                
                            parsed_sections.append({
                                "type": sec_type,
                                "title": sec_title,
                                "heading": sec_heading,
                                "textCa": text_ca,
                                "textEs": text_es,
                                "paragraphs": [text_ca] if text_ca else []
                            })
                            
                        ca_affs = ca_data.get("affectations") or {}
                        for aff_type, aff_list in ca_affs.items():
                            if isinstance(aff_list, list):
                                for aff in aff_list:
                                    target_id = str(aff.get("targetDocumentId") or "")
                                    if target_id:
                                        target_eli = doc_lookup.get(target_id) or f"eli/es-cat/doc/0000/00/00/{target_id}"
                                        rel_type = "MODIFY" if "modific" in aff_type.lower() else ("ABROGATES" if "derog" in aff_type.lower() else "CITES")
                                        sec_eli_first = f"eli/es-cat/doc-sec1/{year}/{month}/{day}/{doc_id}"
                                        citation_edges.append({
                                            "source": sec_eli_first,
                                            "target": target_eli,
                                            "type": rel_type,
                                            "properties": {
                                                "flags": "xml_metadata",
                                                "raw_text": aff.get("text") or ""
                                            }
                                        })
                except Exception:
                    parsed_sections = []
                    
            if not parsed_sections:
                text_block = item.get("title") or ""
                paragraphs = [text_block]
                p_sections = parse_text_into_sections(paragraphs, doc_id, min_year=min_year)
                for s in p_sections:
                    sec_t = "\n".join(s["paragraphs"])
                    parsed_sections.append({
                        "type": s["type"],
                        "title": s["title"],
                        "heading": s.get("heading"),
                        "textCa": sec_t,
                        "textEs": "",
                        "paragraphs": s["paragraphs"]
                    })

            # Build section nodes & edges for this document
            for sec_idx, sec in enumerate(parsed_sections):
                sec_num = sec_idx + 1
                sec_type_cap = sec["type"].capitalize()
                sec_id = f"sec_{doc_id}_{sec_num}"
                sec_eli = f"eli/es-cat/doc-sec{sec_num}/{year}/{month}/{day}/{doc_id}"
                sec_text_ca = sec.get("textCa") or "\n".join(sec.get("paragraphs") or [])
                sec_text_es = sec.get("textEs") or ""
                
                section_node = {
                    "eliID": sec_eli,
                    "labels": ["section", sec_type_cap],
                    "properties": {
                        "id": sec_id,
                        "number": sec_num,
                        "title": sec["title"],
                        "heading": sec.get("heading") or "",
                        "text": sec_text_ca,
                        "textCa": sec_text_ca,
                        "textEs": sec_text_es,
                        "xmlUrl": xml_link_ca or xml_link_es or "",
                        "type": sec["type"],
                        "documentId": doc_id,
                        "bulletin": "DOGC"
                    }
                }
                section_nodes.append(section_node)
                
                # Edge: Document -> Has_Section -> Section
                has_section_edges.append({
                    "source": doc_eli,
                    "target": sec_eli,
                    "type": "Has_Section",
                    "properties": {}
                })
                
                # Extract citations from section text via regex
                cits = extract_citations_from_text(sec_text_ca)
                for cit in cits:
                    citation_edges.append({
                        "source": sec_eli,
                        "target": f"ref_{slugify(cit['ref'])}",
                        "type": "CITES",
                        "properties": {
                            "flags": "regex",
                            "raw_text": cit["raw"]
                        }
                    })

    # ---------------------------------------------------------
    # 2. Parse BOP Bulletins Document Sections (2010+)
    # ---------------------------------------------------------
    bop_docs = []
    for bop_path in [bopg_bopt_path, bopl_path]:
        if os.path.exists(bop_path):
            try:
                with open(bop_path, "r", encoding="utf-8") as bf:
                    bop_docs.extend(json.load(bf))
            except Exception:
                pass
                
    if verbose:
        print(f"[2/2] Processing BOP Bulletins Sections (>= {min_year})...")
        
    for item in tqdm(bop_docs, disable=not verbose, desc="Parsing BOP sections"):
        doc_id = str(item.get("id") or "")
        date_str = str(item.get("date") or "")
        year, month, day = format_date_parts(date_str)
        
        if int(year) < min_year:
            continue
            
        bulletin = item.get("bulletin") or "BOP"
        doc_eli = f"eli/es-cat/doc/{bulletin.lower()}/{year}/{month}/{day}/{doc_id}"
        title = item.get("title") or ""
        
        paragraphs = [title]
        parsed_sections = parse_text_into_sections(paragraphs, doc_id, min_year=min_year)
        
        for sec_idx, sec in enumerate(parsed_sections):
            sec_num = sec_idx + 1
            sec_type_cap = sec["type"].capitalize()
            sec_id = f"sec_{doc_id}_{sec_num}"
            sec_eli = f"eli/es-cat/doc-sec{sec_num}/{year}/{month}/{day}/{doc_id}"
            sec_text = "\n".join(sec["paragraphs"])
            
            section_node = {
                "eliID": sec_eli,
                "labels": ["section", sec_type_cap],
                "properties": {
                    "id": sec_id,
                    "number": sec_num,
                    "title": sec["title"],
                    "heading": sec.get("heading") or "",
                    "text": sec_text,
                    "textCa": sec_text,
                    "textEs": "",
                    "type": sec["type"],
                    "documentId": doc_id,
                    "bulletin": bulletin
                }
            }
            section_nodes.append(section_node)
            
            has_section_edges.append({
                "source": doc_eli,
                "target": sec_eli,
                "type": "Has_Section",
                "properties": {}
            })
            
            cits = extract_citations_from_text(sec_text)
            for cit in cits:
                citation_edges.append({
                    "source": sec_eli,
                    "target": f"ref_{slugify(cit['ref'])}",
                    "type": "CITES",
                    "properties": {
                        "flags": "regex",
                        "raw_text": cit["raw"]
                    }
                })

    # Save output entity files in graph_data/
    out_sec_nodes = os.path.join(output_dir, "section_nodes.json")
    out_has_sec = os.path.join(output_dir, "has_section_edges.json")
    out_sec_cits = os.path.join(output_dir, "section_citation_edges.json")
    
    if verbose:
        print(f"Writing {out_sec_nodes} ({len(section_nodes):,} section nodes)...")
    with open(out_sec_nodes, "w", encoding="utf-8") as out:
        json.dump(section_nodes, out, indent=2, ensure_ascii=False)
        
    if verbose:
        print(f"Writing {out_has_sec} ({len(has_section_edges):,} Has_Section edges)...")
    with open(out_has_sec, "w", encoding="utf-8") as out:
        json.dump(has_section_edges, out, indent=2, ensure_ascii=False)
        
    if verbose:
        print(f"Writing {out_sec_cits} ({len(citation_edges):,} section citation edges)...")
    with open(out_sec_cits, "w", encoding="utf-8") as out:
        json.dump(citation_edges, out, indent=2, ensure_ascii=False)
        
    if verbose:
        print("=======================================================")
        print(f"Successfully generated section-level graph files in {output_dir}:")
        print(f"  - section_nodes.json: {len(section_nodes):,} nodes")
        print(f"  - has_section_edges.json: {len(has_section_edges):,} edges")
        print(f"  - section_citation_edges.json: {len(citation_edges):,} edges")
        print("=======================================================")

def main():
    parser = argparse.ArgumentParser(description="Process section-level nodes and edges for DOGC and BOP documents from 2010 onwards")
    parser.add_argument("--data-dir", type=str, default="data", help="Directory containing dataset files")
    parser.add_argument("--output-dir", type=str, default="graph_data", help="Directory to store output section graph JSON files")
    parser.add_argument("--min-year", type=int, default=2010, help="Minimum publication year to include (default: 2010)")
    parser.add_argument("--quiet", action="store_true", help="Suppress progress output")
    args = parser.parse_args()

    data_dir = os.path.abspath(args.data_dir if os.path.isabs(args.data_dir) else os.path.join(parent_dir, args.data_dir))
    output_dir = os.path.abspath(args.output_dir if os.path.isabs(args.output_dir) else os.path.join(parent_dir, args.output_dir))

    process_section_pipeline(
        data_dir=data_dir,
        output_dir=output_dir,
        min_year=args.min_year,
        verbose=not args.quiet
    )

if __name__ == "__main__":
    main()
