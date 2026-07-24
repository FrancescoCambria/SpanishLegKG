#!/usr/bin/env python3
"""
build_graph_dataset.py

Constructs a unified knowledge graph split into separate JSON files for each entity (node/edge type):
- dogc_nodes.json
- cido_document_nodes.json
- document_nodes.json
- descriptor_nodes.json
- contains_edges.json
- has_topic_edges.json
- citation_affectation_edges.json
- graph_metadata.json

Adheres strictly to graph_data/graph_schema.txt:
- DoGC nodes: eli/es-cat/dogc/{year}/{month}/{day}/{dogcNumber}
- cidoDocument nodes: eli/es-cat/cido-{type_prefix}/{year}/{month}/{day}/{cidoId}
- document nodes: uses existing eliUri if present in data, else eli/es-cat/doc/{year}/{month}/{day}/{documentId}
- descriptor/materia nodes: eli/es-cat/materia/{id}
- Edges: CONTAINS, HAS_TOPIC, CITES, MODIFY, ABROGATES
"""

import os
import re
import sys
import json
import argparse
import unicodedata
from datetime import datetime
from tqdm import tqdm

def to_camel_case_label(text):
    if not text or str(text).lower() in ["unknown", "other", "none", ""]:
        return "Other"
    nfkd_form = unicodedata.normalize('NFKD', str(text))
    ascii_text = nfkd_form.encode('ASCII', 'ignore').decode('utf-8')
    parts = re.split(r'[^a-zA-Z0-9]+', ascii_text)
    camel_parts = [p.capitalize() for p in parts if p]
    if not camel_parts:
        return "Other"
    label = "".join(camel_parts)
    if label[0].isdigit():
        label = "Type_" + label
    return label

def format_date_parts(date_str):
    if not date_str:
        return '0000', '00', '00'
    date_str_clean = str(date_str).strip()
    m = re.match(r'^(\d{1,2})/(\d{1,2})/(\d{4})$', date_str_clean)
    if m:
        d, m_val, y = m.groups()
        return y, m_val.zfill(2), d.zfill(2)
    m2 = re.match(r'^(\d{4})-(\d{1,2})-(\d{1,2})$', date_str_clean)
    if m2:
        y, m_val, d = m2.groups()
        return y, m_val.zfill(2), d.zfill(2)
    m3 = re.search(r'\b(\d{4})\b', date_str_clean)
    if m3:
        return m3.group(1), '00', '00'
    return '0000', '00', '00'

def get_cido_type_prefix(cido_type):
    if not cido_type:
        return "cido-doc"
    t = str(cido_type).lower().strip()
    if "contracta" in t:
        return "cido-cont"
    elif "oposici" in t:
        return "cido-opos"
    elif "normativ" in t:
        return "cido-norm"
    elif "subvenci" in t:
        return "cido-subv"
    elif "conveni" in t:
        return "cido-conv"
    else:
        clean = re.sub(r'[^a-z0-9]', '', t[:8])
        return f"cido-{clean}" if clean else "cido-doc"

def slugify(text):
    if not text:
        return ""
    nfkd_form = unicodedata.normalize('NFKD', str(text))
    ascii_text = nfkd_form.encode('ASCII', 'ignore').decode('utf-8')
    return re.sub(r'[^a-z0-9]+', '_', ascii_text.lower()).strip('_')

def build_graph(data_dir, output_dir, include_structured=True, include_sections=False, verbose=True):
    os.makedirs(output_dir, exist_ok=True)
    
    dogc_path = os.path.join(data_dir, "dogc_documents.json")
    cido_path = os.path.join(data_dir, "cido_documents.json")
    if not os.path.exists(cido_path) and os.path.exists(os.path.join(data_dir, "cido_to_dogc_map.json")):
        cido_path = os.path.join(data_dir, "cido_to_dogc_map.json")
    structured_dir = os.path.join(data_dir, "structured_output")
    
    # Storage per entity type
    dogc_nodes_list = []
    cido_nodes_list = []
    document_nodes_list = []
    descriptor_nodes_list = []
    
    contains_edges_list = []
    has_topic_edges_list = []
    citation_edges_list = []
    
    # Trackers for deduplication
    dogc_nodes_idx = {}       # dogcNumber -> eliID
    doc_nodes_idx = {}        # documentId -> eliID
    cido_nodes_idx = {}       # cidoId -> eliID
    descriptor_nodes_idx = {} # descriptor_id -> eliID
    
    # ---------------------------------------------------------
    # 1. Parse DOGC Documents Dataset
    # ---------------------------------------------------------
    if os.path.exists(dogc_path):
        if verbose:
            print(f"[1/3] Loading DOGC documents from {dogc_path}...")
        with open(dogc_path, "r", encoding="utf-8") as f:
            dogc_docs = json.load(f)
        if verbose:
            print(f"Processing {len(dogc_docs)} DOGC documents...")
            
        for item in tqdm(dogc_docs, disable=not verbose, desc="Parsing DOGC docs"):
            doc_id = str(item.get("documentId") or "")
            if not doc_id:
                continue
                
            dogc_num = str(item.get("dogcNumber") or "")
            date_dogc = item.get("dateDOGC")
            year, month, day = format_date_parts(date_dogc)
            if not year or year == "0000":
                year = str(item.get("year") or "0000")
                
            # Create/Get DoGC Node
            if dogc_num and dogc_num != "None":
                if dogc_num not in dogc_nodes_idx:
                    dogc_eli = f"eli/es-cat/dogc/{year}/{month}/{day}/{dogc_num}"
                    dogc_url = f"https://dogc.gencat.cat/ca/document-del-dogc/index.html?numDOGC={dogc_num}"
                    dogc_node = {
                        "eliID": dogc_eli,
                        "labels": ["DoGC"],
                        "properties": {
                            "date": date_dogc or f"{day}/{month}/{year}",
                            "dogcNumber": dogc_num,
                            "urllink": dogc_url,
                            "year": year
                        }
                    }
                    dogc_nodes_idx[dogc_num] = dogc_eli
                    dogc_nodes_list.append(dogc_node)
                else:
                    dogc_eli = dogc_nodes_idx[dogc_num]
            else:
                dogc_eli = None

            # Check if structured output JSON exists for this document
            structured_data = None
            if include_structured and os.path.exists(structured_dir):
                struct_file = os.path.join(structured_dir, f"dogc_doc_{doc_id}_structured.json")
                if os.path.exists(struct_file):
                    try:
                        with open(struct_file, "r", encoding="utf-8") as sf:
                            structured_data = json.load(sf)
                    except Exception:
                        structured_data = None

            # Determine document ELI ID
            doc_eli = None
            if structured_data and structured_data.get("eliUri"):
                doc_eli = structured_data.get("eliUri")
            elif item.get("eliUri"):
                doc_eli = item.get("eliUri")
            else:
                doc_eli = f"eli/es-cat/doc/{year}/{month}/{day}/{doc_id}"
                
            doc_type = item.get("type") or "DOCUMENT"
            specific_type_label = to_camel_case_label(doc_type)
            
            doc_node = {
                "eliID": doc_eli,
                "labels": ["document", specific_type_label],
                "properties": {
                    "documentId": doc_id,
                    "title": item.get("title") or "",
                    "date": date_dogc or f"{day}/{month}/{year}",
                    "urllink": item.get("htmlUrl") or f"https://dogc.gencat.cat/ca/document-del-dogc/index.html?documentId={doc_id}",
                    "pdfUrl": item.get("pdfUrl") or "",
                    "organisme": item.get("organisme") or "",
                    "dogcNumber": dogc_num,
                    "type": doc_type
                }
            }
            
            doc_nodes_idx[doc_id] = doc_eli
            document_nodes_list.append(doc_node)
            
            # Edge: DoGC -> CONTAINS -> document
            if dogc_eli:
                contains_edges_list.append({
                    "source": dogc_eli,
                    "target": doc_eli,
                    "type": "CONTAINS",
                    "properties": {
                        "macrosection": item.get("section") or "Disposicions generals"
                    }
                })
                
            # Process Descriptors if available in structured data
            if structured_data:
                ca_data = structured_data.get("ca") or {}
                descriptors = ca_data.get("descriptors") or {}
                for category, desc_list in descriptors.items():
                    if isinstance(desc_list, list):
                        for desc_item in desc_list:
                            if not desc_item:
                                continue
                            if isinstance(desc_item, dict):
                                desc_name = desc_item.get("name") or desc_item.get("title") or str(desc_item)
                                raw_id = desc_item.get("id") or desc_name
                            else:
                                desc_name = str(desc_item)
                                raw_id = desc_name
                                
                            desc_id = slugify(raw_id)
                            if not desc_id:
                                continue
                            desc_eli = f"eli/es-cat/materia/{desc_id}"
                            if desc_id not in descriptor_nodes_idx:
                                descriptor_nodes_idx[desc_id] = desc_eli
                                descriptor_nodes_list.append({
                                    "eliID": desc_eli,
                                    "labels": ["descriptor", "materia"],
                                    "properties": {
                                        "id": desc_id,
                                        "name": desc_name,
                                        "category": category
                                    }
                                })
                            has_topic_edges_list.append({
                                "source": doc_eli,
                                "target": desc_eli,
                                "type": "HAS_TOPIC",
                                "properties": {}
                            })
                            
                # Process affectations (citations / modify / abrogate)
                affectations = ca_data.get("affectations") or {}
                for aff_type, aff_list in affectations.items():
                    if isinstance(aff_list, list):
                        for aff_item in aff_list:
                            target_doc_id = str(aff_item.get("targetDocumentId") or aff_item.get("documentId") or "")
                            if target_doc_id:
                                target_eli = doc_nodes_idx.get(target_doc_id) or f"eli/es-cat/doc/0000/00/00/{target_doc_id}"
                                rel_type = "MODIFY" if "modific" in aff_type.lower() else ("ABROGATES" if "derog" in aff_type.lower() else "CITES")
                                citation_edges_list.append({
                                    "source": doc_eli,
                                    "target": target_eli,
                                    "type": rel_type,
                                    "properties": {
                                        "flags": "metadata",
                                        "raw_text": aff_item.get("text") or ""
                                    }
                                })
    else:
        if verbose:
            print(f"Warning: {dogc_path} not found.")

    # ---------------------------------------------------------
    # 2. Parse CIDO Documents Map
    # ---------------------------------------------------------
    if os.path.exists(cido_path):
        if verbose:
            print(f"[2/3] Loading CIDO map from {cido_path}...")
        with open(cido_path, "r", encoding="utf-8") as f:
            cido_docs = json.load(f)
        if verbose:
            print(f"Processing {len(cido_docs)} CIDO records...")
            
        for item in tqdm(cido_docs, disable=not verbose, desc="Parsing CIDO docs"):
            cido_id = str(item.get("cidoId") or "")
            if not cido_id:
                continue
                
            raw_type = item.get("type") or "cidoDocument"
            type_prefix = get_cido_type_prefix(raw_type)
            specific_type_label = to_camel_case_label(raw_type)
            
            date_val = item.get("date")
            year, month, day = format_date_parts(date_val)
            
            cido_eli = f"eli/es-cat/{type_prefix}/{year}/{month}/{day}/{cido_id}"
            cido_node = {
                "eliID": cido_eli,
                "labels": ["cidoDocument", specific_type_label],
                "properties": {
                    "cidoId": cido_id,
                    "title": item.get("title") or "",
                    "date": date_val or f"{year}-{month}-{day}",
                    "urllink": item.get("urlCido") or f"https://cido.diba.cat/{type_prefix}/{cido_id}",
                    "identificador": item.get("identificador") or "",
                    "institucio": item.get("institucio") or "",
                    "esVigent": item.get("esVigent") if item.get("esVigent") is not None else True,
                    "location": item.get("location") or {}
                }
            }
            cido_nodes_idx[cido_id] = cido_eli
            cido_nodes_list.append(cido_node)
            
            # Process CIDO Materies / Descriptors if available
            cido_materies = item.get("materies") or item.get("detalls", {}).get("materies") or []
            for m_item in cido_materies:
                if isinstance(m_item, dict):
                    m_name = m_item.get("name") or m_item.get("materia") or ""
                    raw_m_id = m_item.get("id") or m_name
                else:
                    m_name = str(m_item)
                    raw_m_id = m_name
                    
                m_id = slugify(raw_m_id)
                if not m_id:
                    continue
                m_eli = f"eli/es-cat/materia/{m_id}"
                if m_id not in descriptor_nodes_idx:
                    descriptor_nodes_idx[m_id] = m_eli
                    descriptor_nodes_list.append({
                        "eliID": m_eli,
                        "labels": ["descriptor", "materia"],
                        "properties": {
                            "id": m_id,
                            "name": m_name,
                            "category": "cido_materia"
                        }
                    })
                has_topic_edges_list.append({
                    "source": cido_eli,
                    "target": m_eli,
                    "type": "HAS_TOPIC",
                    "properties": {}
                })
            
            # Process linked documents in CIDO record
            docs_in_cido = item.get("documents") or []
            for idx, c_doc in enumerate(docs_in_cido):
                target_doc_id = c_doc.get("dogcDocumentId")
                if target_doc_id and str(target_doc_id) in doc_nodes_idx:
                    target_eli = doc_nodes_idx[str(target_doc_id)]
                else:
                    # Non-DOGC document or unmatched PDF (local bulletin, etc.)
                    local_doc_id = f"cido_doc_{cido_id}_{idx}"
                    target_eli = f"eli/es-cat/doc/local/{cido_id}/{idx}"
                    document_nodes_list.append({
                        "eliID": target_eli,
                        "labels": ["document", "LocalBulletin"],
                        "properties": {
                            "documentId": local_doc_id,
                            "title": c_doc.get("descripcio") or item.get("title") or "",
                            "date": c_doc.get("dataPublicacio") or date_val or "",
                            "urllink": c_doc.get("urlPdf") or c_doc.get("urlHtml") or item.get("urlCido") or "",
                            "pdfUrl": c_doc.get("urlPdf") or "",
                            "butlleti": c_doc.get("butlleti") or "",
                            "allSources": c_doc.get("allSources") or ([c_doc.get("butlleti")] if c_doc.get("butlleti") else []),
                            "isMultiSource": c_doc.get("isMultiSource", False),
                            "isUrlActive": c_doc.get("isUrlActive", True),
                            "urlStatus": c_doc.get("urlStatus", "active"),
                            "type": "LocalBulletin"
                        }
                    })
                        
                contains_edges_list.append({
                    "source": cido_eli,
                    "target": target_eli,
                    "type": "CONTAINS",
                    "properties": {
                        "fase": c_doc.get("fase") or "",
                        "butlleti": c_doc.get("butlleti") or "",
                        "numButlleti": c_doc.get("numButlleti") or 0,
                        "dataPublicacio": c_doc.get("dataPublicacio") or "",
                        "allSources": c_doc.get("allSources") or ([c_doc.get("butlleti")] if c_doc.get("butlleti") else []),
                        "isMultiSource": c_doc.get("isMultiSource", False),
                        "isUrlActive": c_doc.get("isUrlActive", True),
                        "urlStatus": c_doc.get("urlStatus", "active")
                    }
                })
    else:
        if verbose:
            print(f"Warning: {cido_path} not found.")

    # Check if section files exist or include_sections is enabled
    sec_nodes_path = os.path.join(output_dir, "section_nodes.json")
    has_sec_path = os.path.join(output_dir, "has_section_edges.json")
    sec_cit_path = os.path.join(output_dir, "section_citation_edges.json")
    
    sec_nodes_count = 0
    has_sec_count = 0
    sec_cit_count = 0

    if include_sections or (os.path.exists(sec_nodes_path) and os.path.exists(has_sec_path)):
        if os.path.exists(sec_nodes_path):
            with open(sec_nodes_path, "r", encoding="utf-8") as f:
                sec_nodes_count = len(json.load(f))
        if os.path.exists(has_sec_path):
            with open(has_sec_path, "r", encoding="utf-8") as f:
                has_sec_count = len(json.load(f))
        if os.path.exists(sec_cit_path):
            with open(sec_cit_path, "r", encoding="utf-8") as f:
                sec_cit_count = len(json.load(f))

    files_to_save = {
        "dogc_nodes.json": dogc_nodes_list,
        "cido_document_nodes.json": cido_nodes_list,
        "document_nodes.json": document_nodes_list,
        "descriptor_nodes.json": descriptor_nodes_list,
        "contains_edges.json": contains_edges_list,
        "has_topic_edges.json": has_topic_edges_list,
        "citation_affectation_edges.json": citation_edges_list
    }
    
    total_nodes_count = len(dogc_nodes_list) + len(cido_nodes_list) + len(document_nodes_list) + len(descriptor_nodes_list) + sec_nodes_count
    total_edges_count = len(contains_edges_list) + len(has_topic_edges_list) + len(citation_edges_list) + has_sec_count + sec_cit_count
    
    metadata = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "schema_version": "1.0",
        "total_nodes": total_nodes_count,
        "total_edges": total_edges_count,
        "node_counts": {
            "DoGC": len(dogc_nodes_list),
            "cidoDocument": len(cido_nodes_list),
            "document": len(document_nodes_list),
            "descriptor": len(descriptor_nodes_list),
            "section": sec_nodes_count
        },
        "edge_counts": {
            "CONTAINS": len(contains_edges_list),
            "HAS_TOPIC": len(has_topic_edges_list),
            "CITATIONS_AFFECTATIONS": len(citation_edges_list),
            "HAS_SECTION": has_sec_count,
            "SECTION_CITATIONS": sec_cit_count
        },
        "files": list(files_to_save.keys()) + (["section_nodes.json", "has_section_edges.json", "section_citation_edges.json"] if sec_nodes_count > 0 else [])
    }
    
    if verbose:
        print(f"[3/3] Saving split entity JSON files to {output_dir}...")
        
    for fname, data_list in files_to_save.items():
        fpath = os.path.join(output_dir, fname)
        if verbose:
            print(f"Writing {fname} ({len(data_list):,} entries)...")
        with open(fpath, "w", encoding="utf-8") as out:
            json.dump(data_list, out, indent=2, ensure_ascii=False)
            
    # Save graph_metadata.json
    meta_path = os.path.join(output_dir, "graph_metadata.json")
    with open(meta_path, "w", encoding="utf-8") as out:
        json.dump(metadata, out, indent=2, ensure_ascii=False)

    if verbose:
        print("=======================================================")
        print(f"Successfully generated all entity files in {output_dir}:")
        print(f"  - dogc_nodes.json: {len(dogc_nodes_list):,} nodes")
        print(f"  - cido_document_nodes.json: {len(cido_nodes_list):,} nodes")
        print(f"  - document_nodes.json: {len(document_nodes_list):,} nodes")
        print(f"  - descriptor_nodes.json: {len(descriptor_nodes_list):,} nodes")
        print(f"  - contains_edges.json: {len(contains_edges_list):,} edges")
        print(f"  - has_topic_edges.json: {len(has_topic_edges_list):,} edges")
        print(f"  - citation_affectation_edges.json: {len(citation_edges_list):,} edges")
        print(f"  - graph_metadata.json (Summary)")
        print("=======================================================")

def main():
    parser = argparse.ArgumentParser(description="Build Knowledge Graph dataset split into separate entity JSON files")
    parser.add_argument("--data-dir", type=str, default="data", help="Directory containing scraped JSON data files")
    parser.add_argument("--output-dir", type=str, default="graph_data", help="Directory to store resulting split JSON files")
    parser.add_argument("--no-structured", action="store_true", help="Disable loading structured_output JSON enrichment")
    parser.add_argument("--no-sections", action="store_true", help="Disable section level nodes/edges processing")
    parser.add_argument("--quiet", action="store_true", help="Suppress progress output")
    args = parser.parse_args()
    
    script_dir = os.path.dirname(os.path.abspath(__file__))
    parent_dir = os.path.dirname(script_dir)
    
    data_dir = os.path.abspath(args.data_dir if os.path.isabs(args.data_dir) else os.path.join(parent_dir, args.data_dir))
    output_dir = os.path.abspath(args.output_dir if os.path.isabs(args.output_dir) else os.path.join(parent_dir, args.output_dir))
    
    build_graph(
        data_dir=data_dir,
        output_dir=output_dir,
        include_structured=not args.no_structured,
        include_sections=not args.no_sections,
        verbose=not args.quiet
    )

if __name__ == "__main__":
    main()
