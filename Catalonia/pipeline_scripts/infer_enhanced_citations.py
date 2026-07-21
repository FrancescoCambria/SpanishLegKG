import os
import sys
import json
import argparse
import re

def extract_type_from_string(text):
    if not text:
        return None
    patterns = [
        (r'\bdecret\s+legislatiu\b|\bdecreto\s+legislativo\b', 'Decret legislatiu'),
        (r'\bdecret\b|\bdecreto\b', 'Decret'),
        (r'\bresoluci[óo]\b|\bresoluci[oó]n\b', 'Resolució'),
        (r'\bedicte\b|\bedicto\b', 'Edicte'),
        (r'\banunci\b|\banuncio\b', 'Anunci'),
        (r'\bordre\b|\borden\b', 'Ordre'),
        (r'\bllei\b|\bley\b', 'Llei'),
        (r'\bacord\b|\bacuerdo\b', 'Acord'),
        (r'\binstrucci[óo]\b|\binstrucci[oó]n\b', 'Instrucció'),
        (r'\bconvenis?\b|\bconvenios?\b', 'Convenis'),
        (r'\bnotificaci[óo]\b|\bnotificaci[oó]n\b', 'Notificació'),
    ]
    for pattern, name in patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return name
    return None

def get_node_title(node):
    if not node:
        return "Unknown Node"
    props = node.get("properties", {})
    return props.get("title") or props.get("titleEs") or props.get("titleCa") or "Untitled"

def get_document_type(node, doc_by_id, has_sec_map, backup_docs):
    if not node:
        return "Unknown"
    labels = node.get("labels", [])
    props = node.get("properties", {})

    # 1. Direct Document node
    if "Document" in labels:
        tol = props.get("typeOfLaw")
        if tol and str(tol).strip():
            return str(tol).strip()
        non_doc = [l for l in labels if l not in ("Document", "DocumentSection", "Article")]
        if non_doc:
            return non_doc[0]
        title = props.get("title") or props.get("titleCa") or props.get("titleEs")
        t_type = extract_type_from_string(title)
        if t_type:
            return t_type
        return "Document"

    # 2. Check parent via HAS_SECTION
    parent = has_sec_map.get(node.get("id"))
    if parent:
        return get_document_type(parent, doc_by_id, has_sec_map, backup_docs)

    # 3. Check sectionId prefix (documentId) in graph or backup docs
    sec_id = str(props.get("sectionId", ""))
    if "_" in sec_id:
        doc_id = sec_id.split("_")[0]
        parent = doc_by_id.get(doc_id)
        if parent:
            return get_document_type(parent, doc_by_id, has_sec_map, backup_docs)
        
        b_doc = backup_docs.get(doc_id)
        if b_doc:
            b_tol = b_doc.get("typeOfLaw")
            if b_tol and str(b_tol).strip():
                return str(b_tol).strip()
            b_title = b_doc.get("title") or b_doc.get("titleCa") or b_doc.get("titleEs")
            t_type = extract_type_from_string(b_title)
            if t_type:
                return t_type

    # 4. Fallback heuristics: non-Document labels, title, or section text
    non_doc = [l for l in labels if l not in ("Document", "DocumentSection", "Article")]
    if non_doc:
        return non_doc[0]

    title = props.get("title") or props.get("titleCa") or props.get("titleEs")
    t_type = extract_type_from_string(title)
    if t_type:
        return t_type

    text = props.get("textCa") or props.get("textEs") or props.get("text")
    t_type = extract_type_from_string(text)
    if t_type:
        return t_type

    return props.get("type") or "Unknown"

def main():
    parser = argparse.ArgumentParser(description="Query enhanced citations by Node ID")
    parser.add_argument("node_id", type=int, help="ID of the node to inspect")
    parser.add_argument("--orig-json", default="data/extracted_subgraph_custom.json", help="Path to original subgraph JSON")
    parser.add_argument("--upd-json", default="data/extracted_subgraph_custom_updated.json", help="Path to updated subgraph JSON")
    args = parser.parse_args()

    # Resolve paths relative to Catalonia workspace root
    script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    orig_path = os.path.join(script_dir, args.orig_json)
    upd_path = os.path.join(script_dir, args.upd_json)

    if not os.path.exists(orig_path):
        print(f"Error: Original JSON file not found at {orig_path}")
        sys.exit(1)
    if not os.path.exists(upd_path):
        print(f"Error: Updated JSON file not found at {upd_path}")
        sys.exit(1)

    print("Loading subgraphs...")
    with open(orig_path, "r", encoding="utf-8") as f:
        orig_data = json.load(f)
    with open(upd_path, "r", encoding="utf-8") as f:
        upd_data = json.load(f)

    # Index nodes
    orig_nodes = {n["id"]: n for n in orig_data["nodes"]}
    upd_nodes = {n["id"]: n for n in upd_data["nodes"]}

    if args.node_id not in upd_nodes:
        print(f"Error: Node ID {args.node_id} not found in the updated subgraph.")
        sys.exit(1)

    # Map Document nodes by documentId and id
    doc_by_id = {}
    for n in upd_data["nodes"]:
        did = n.get("properties", {}).get("documentId")
        if did:
            doc_by_id[str(did)] = n
        doc_by_id[str(n["id"])] = n

    # Map HAS_SECTION relationships
    has_sec_map = {}
    for r in upd_data["relationships"]:
        if r["type"] == "HAS_SECTION":
            has_sec_map[r["target"]] = upd_nodes.get(r["source"])

    # Load backup JSONs for document metadata if present
    backup_docs = {}
    for b_file in ["data/dogc_documents_2024_2026_backup.json", "data/dogc_documents_backup_full.json"]:
        b_path = os.path.join(script_dir, b_file)
        if os.path.exists(b_path):
            try:
                with open(b_path, "r", encoding="utf-8") as f:
                    bd = json.load(f)
                for d in bd:
                    did = str(d.get("documentId") or d.get("id") or "")
                    if did and did not in backup_docs:
                        backup_docs[did] = d
            except Exception:
                pass

    node = upd_nodes[args.node_id]
    props = node.get("properties", {})
    node_doc_type = get_document_type(node, doc_by_id, has_sec_map, backup_docs)
    
    print("=" * 70)
    print(f"NODE DETAILS (ID: {args.node_id})")
    print("=" * 70)
    print(f"Labels:        {', '.join(node.get('labels', []))}")
    print(f"Title:         {get_node_title(node)}")
    print(f"Doc Type:      {node_doc_type}")
    print(f"Section Type:  {props.get('type', 'N/A')}")
    print(f"Processed:     {props.get('processed_by_llm_agent', False)}")
    
    text = props.get("textEs") or props.get("textCa") or props.get("text")
    print("-" * 70)
    print("NODE TEXT:")
    print("-" * 70)
    if text:
        print(text)
    else:
        print("[No text content]")
    print("-" * 70)

    # Gather relationships where this node is the source
    orig_rels = [r for r in orig_data["relationships"] if r["source"] == args.node_id]
    upd_rels = [r for r in upd_data["relationships"] if r["source"] == args.node_id]

    # Find new relationships
    orig_keys = {(r["source"], r["target"], r["type"]) for r in orig_rels}
    new_rels = [r for r in upd_rels if (r["source"], r["target"], r["type"]) not in orig_keys]

    print("\nORIGINAL CITATIONS:")
    print("-" * 70)
    if orig_rels:
        for idx, r in enumerate(orig_rels):
            target_node = upd_nodes.get(r["target"])
            target_title = get_node_title(target_node)
            target_labels = ", ".join(target_node.get("labels", [])) if target_node else "Unknown"
            target_doc_type = get_document_type(target_node, doc_by_id, has_sec_map, backup_docs)
            
            p = r.get("properties", {})
            cited_doc = p.get("citedDocument") or p.get("cited_text") or ""
            cited_sec = p.get("citedSection") or p.get("details") or ""
            
            citation_str = cited_doc
            if cited_sec:
                citation_str += f" ({cited_sec})"
            
            print(f"  {idx+1}. [{r['type']}] -> Node {r['target']} ({target_labels}) [Doc Type: {target_doc_type}]")
            print(f"     Title: {target_title}")
            print(f"     Text extracted: '{citation_str}'")
    else:
        print("  [No original citations found]")

    print("\nNEW CITATIONS ADDED BY LLM:")
    print("-" * 70)
    if new_rels:
        for idx, r in enumerate(new_rels):
            target_node = upd_nodes.get(r["target"])
            target_title = get_node_title(target_node)
            target_labels = ", ".join(target_node.get("labels", [])) if target_node else "Unknown"
            target_doc_type = get_document_type(target_node, doc_by_id, has_sec_map, backup_docs)
            
            p = r.get("properties", {})
            cited_text = p.get("cited_text") or p.get("citedDocument") or ""
            details = p.get("details") or p.get("citedSection") or ""
            
            citation_str = cited_text
            if details:
                citation_str += f" ({details})"
                
            print(f"  {idx+1}. [{r['type']}] -> Node {r['target']} ({target_labels}) [Doc Type: {target_doc_type}]")
            print(f"     Title: {target_title}")
            print(f"     Text extracted: '{citation_str}'")
            if target_node and target_node.get("properties", {}).get("created_by_llm_agent"):
                print("     [Note: Target node was also CREATED by the LLM agent]")
    else:
        print("  [No new citations added by LLM]")
    print("=" * 70)

if __name__ == "__main__":
    main()

