import os
import sys
import json
import re
import time

def get_link_to_text(html_url, pdf_url, fallback=None):
    if html_url and isinstance(html_url, str) and html_url.strip():
        return html_url.strip()
    if pdf_url and isinstance(pdf_url, str) and pdf_url.strip():
        return pdf_url.strip()
    if fallback and isinstance(fallback, str) and fallback.strip():
        return fallback.strip()
    return None

def extract_article_number(title):
    if not title:
        return None
    m = re.search(r'\b(?:article|artículo|art\.?)\s+([0-9a-zA-Z\-]+)', title, re.IGNORECASE)
    if m:
        return m.group(1).capitalize()
    return None

def main():
    start_time = time.time()
    data_dir = "/home/cambria/gram3/LawGraph/Spain/Catalonia/data"
    output_dir = os.path.join(data_dir, "hierarchical_output")
    os.makedirs(output_dir, exist_ok=True)
    
    cido_path = os.path.join(data_dir, "cido_to_dogc_map.json")
    dogc_path = os.path.join(data_dir, "dogc_documents.json")
    sec_rels_path = os.path.join(data_dir, "prepared_graph_data/has_section_relationships.json")
    sec_nodes_path = os.path.join(data_dir, "prepared_graph_data/document_sections.json")
    prep_docs_path = os.path.join(data_dir, "prepared_graph_data/document_nodes.json")
    
    # 0. Load ELI numbers from prepared_graph_data
    eli_map = {}
    if os.path.exists(prep_docs_path):
        print(f"Loading ELI numbers from {prep_docs_path}...")
        with open(prep_docs_path, "r", encoding="utf-8") as f:
            prep_docs = json.load(f)
        for d in prep_docs:
            props = d.get("properties", {})
            doc_id = str(props.get("documentId"))
            eli = props.get("eliUri")
            if doc_id and eli and isinstance(eli, str) and eli.strip():
                eli_map[doc_id] = eli.strip()
        print(f"Loaded ELI numbers for {len(eli_map)} documents.")

    print("\n--- Step 1: Loading Section Relationships & Section Nodes ---")
    doc_to_sections = {}
    if os.path.exists(sec_rels_path):
        with open(sec_rels_path, "r", encoding="utf-8") as f:
            sec_rels = json.load(f)
        for rel in sec_rels:
            doc_id = str(rel.get("documentId"))
            sec_id = rel.get("sectionId")
            if doc_id and sec_id:
                if doc_id not in doc_to_sections:
                    doc_to_sections[doc_id] = []
                doc_to_sections[doc_id].append(sec_id)
        print(f"Loaded section relationships for {len(doc_to_sections)} documents.")
    
    section_nodes = []
    seen_section_ids = set()
    if os.path.exists(sec_nodes_path):
        with open(sec_nodes_path, "r", encoding="utf-8") as f:
            sec_nodes_raw = json.load(f)
        for s in sec_nodes_raw:
            sec_id = s.get("sectionId")
            if sec_id and sec_id not in seen_section_ids:
                seen_section_ids.add(sec_id)
                s_props = s.get("properties", {})
                s_title = s_props.get("title") or s_props.get("heading") or s.get("specificLabel") or "Section"
                s_heading = s_props.get("heading")
                s_type = s_props.get("type") or s.get("specificLabel")
                
                s_full_title = f"{s_title} - {s_heading}" if s_heading and s_title != s_heading else s_title
                doc_id = sec_id.split("_sec_")[0]
                art_num = extract_article_number(s_title)
                
                # Section Node with section_type and article_number (WITHOUT text/link)
                section_nodes.append({
                    "id": sec_id,
                    "title": s_full_title,
                    "document_id": doc_id,
                    "section_type": s_type,
                    "article_number": art_num
                })
        print(f"Loaded {len(section_nodes)} section nodes with type & article number.")

    # Write Section Nodes JSON
    sec_out_file = os.path.join(output_dir, "section_nodes.json")
    print(f"Writing {sec_out_file}...")
    with open(sec_out_file, "w", encoding="utf-8") as out:
        json.dump(section_nodes, out, indent=2, ensure_ascii=False)
    print(f"Saved {sec_out_file} ({os.path.getsize(sec_out_file) / (1024*1024):.2f} MB)")
    del section_nodes, sec_nodes_raw
    
    print("\n--- Step 2: Processing CIDO Conglomerated Nodes & Non-DOGC Documents ---")
    print(f"Loading {cido_path}...")
    with open(cido_path, "r", encoding="utf-8") as f:
        cido_list = json.load(f)
    print(f"Loaded {len(cido_list)} CIDO entries.")
    
    cido_nodes = []
    non_dogc_document_nodes = []
    dogc_to_cido_map = {}
    
    for item in cido_list:
        cido_id = str(item.get("cidoId"))
        title = item.get("title")
        date_str = item.get("date")
        url_cido = item.get("urlCido")
        
        raw_docs = item.get("documents") or []
        doc_ids = []
        
        for idx, d in enumerate(raw_docs):
            d_dogc_id = str(d.get("dogcDocumentId")) if d.get("dogcDocumentId") else None
            
            if d_dogc_id:
                if d_dogc_id not in doc_ids:
                    doc_ids.append(d_dogc_id)
                if d_dogc_id not in dogc_to_cido_map:
                    dogc_to_cido_map[d_dogc_id] = []
                if cido_id not in dogc_to_cido_map[d_dogc_id]:
                    dogc_to_cido_map[d_dogc_id].append(cido_id)
            else:
                synthetic_doc_id = f"cido_{cido_id}_doc_{idx}"
                doc_ids.append(synthetic_doc_id)
                
                doc_title = d.get("descripcio") or title or "Document"
                doc_date = d.get("dataPublicacio") or date_str
                link_to_text = get_link_to_text(d.get("urlHtml"), d.get("urlPdf"))
                bulletin = d.get("butlleti") or "CIDO"
                num_butlleti = str(d.get("numButlleti")) if d.get("numButlleti") is not None else None
                
                non_dogc_document_nodes.append({
                    "id": synthetic_doc_id,
                    "title": doc_title,
                    "parent_cido_id": cido_id,
                    "date": doc_date,
                    "dogc_number": num_butlleti,
                    "eli_number": None,
                    "link_to_text": link_to_text,
                    "bulletin": bulletin,
                    "section_ids": []
                })
                    
        cido_nodes.append({
            "id": cido_id,
            "title": title,
            "date": date_str,
            "urlCido": url_cido,
            "document_ids": doc_ids
        })

    print(f"Processed {len(cido_nodes)} CIDO conglomerated nodes.")
    print(f"Processed {len(non_dogc_document_nodes)} non-DOGC document nodes.")

    # Write CIDO Nodes JSON
    cido_out_file = os.path.join(output_dir, "cido_nodes.json")
    print(f"Writing {cido_out_file}...")
    with open(cido_out_file, "w", encoding="utf-8") as out:
        json.dump(cido_nodes, out, indent=2, ensure_ascii=False)
    print(f"Saved {cido_out_file} ({os.path.getsize(cido_out_file) / (1024*1024):.2f} MB)")
    del cido_nodes, cido_list

    print("\n--- Step 3: Processing DOGC Document Nodes & Merging All Documents ---")
    print(f"Loading {dogc_path}...")
    with open(dogc_path, "r", encoding="utf-8") as f:
        dogc_list = json.load(f)
    print(f"Loaded {len(dogc_list)} DOGC documents.")
    
    all_document_nodes = []
    
    # 1. Add official DOGC document nodes
    for doc in dogc_list:
        doc_id = str(doc.get("documentId"))
        title = doc.get("title")
        date_str = doc.get("dateDOGC")
        dogc_num = str(doc.get("dogcNumber")) if doc.get("dogcNumber") is not None else None
        html_url = doc.get("htmlUrl")
        pdf_url = doc.get("pdfUrl")
        
        doc_link = get_link_to_text(html_url, pdf_url)
        sec_ids = doc_to_sections.get(doc_id, [])
        cido_parents = dogc_to_cido_map.get(doc_id)
        if cido_parents:
            parent_cido_id = cido_parents[0] if len(cido_parents) == 1 else cido_parents
        else:
            parent_cido_id = None
            
        eli_num = eli_map.get(doc_id)
            
        all_document_nodes.append({
            "id": doc_id,
            "title": title,
            "parent_cido_id": parent_cido_id,
            "date": date_str,
            "dogc_number": dogc_num,
            "eli_number": eli_num,
            "link_to_text": doc_link,
            "bulletin": "DOGC",
            "section_ids": sec_ids
        })

    print(f"Processed {len(all_document_nodes)} official DOGC document nodes.")
    
    # 2. Append non-DOGC document nodes
    all_document_nodes.extend(non_dogc_document_nodes)
    print(f"Total merged document nodes (DOGC + Non-DOGC): {len(all_document_nodes)}")
    del non_dogc_document_nodes, dogc_list, eli_map

    print("\n--- Step 4: Writing Final document_nodes.json ---")
    doc_out_file = os.path.join(output_dir, "document_nodes.json")
    print(f"Writing {doc_out_file}...")
    with open(doc_out_file, "w", encoding="utf-8") as out:
        json.dump(all_document_nodes, out, indent=2, ensure_ascii=False)
    print(f"Saved {doc_out_file} ({os.path.getsize(doc_out_file) / (1024*1024):.2f} MB)")

    print(f"\nCompleted Option B v2 build in {time.time() - start_time:.2f} seconds.")

if __name__ == "__main__":
    main()
