import os
import sys
import json
import argparse
from collections import deque, defaultdict
from neo4j import GraphDatabase

def fetch_graph_metadata(session):
    print("Fetching DocumentSection metadata...")
    sections = {}
    query_sections = """
    MATCH (s:DocumentSection)
    RETURN id(s) AS id, labels(s) AS labels, 
           coalesce(size(s.text), 0) + coalesce(size(s.textCa), 0) + coalesce(size(s.textEs), 0) + coalesce(size(s.title), 0) + coalesce(size(s.heading), 0) AS char_len
    """
    res = session.run(query_sections)
    for r in res:
        sections[r["id"]] = (list(r["labels"]), r["char_len"])

    print(f"Fetched {len(sections)} DocumentSection nodes.")

    print("Fetching Document metadata...")
    documents = {}
    query_docs = """
    MATCH (d:Document)
    RETURN id(d) AS id, labels(d) AS labels,
           coalesce(size(d.title), 0) + coalesce(size(d.titleCa), 0) + coalesce(size(d.titleEs), 0) AS char_len
    """
    res = session.run(query_docs)
    for r in res:
        documents[r["id"]] = (list(r["labels"]), r["char_len"])

    print(f"Fetched {len(documents)} Document nodes.")

    print("Fetching Section-to-Section relationships...")
    sec_to_sec = []
    query_s2s = """
    MATCH (s1:DocumentSection)-[r]->(s2:DocumentSection)
    RETURN id(s1) AS id1, id(s2) AS id2, type(r) AS type
    """
    res = session.run(query_s2s)
    for r in res:
        sec_to_sec.append((r["id1"], r["id2"], r["type"]))

    print(f"Fetched {len(sec_to_sec)} Section-to-Section relationships.")

    print("Fetching Section-to-Document relationships...")
    sec_to_doc = []
    query_s2d = """
    MATCH (s:DocumentSection)-[r]->(d:Document)
    RETURN id(s) AS s_id, id(d) AS d_id, type(r) AS type
    """
    res = session.run(query_s2d)
    for r in res:
        sec_to_doc.append((r["s_id"], r["d_id"], r["type"]))

    query_d2s = """
    MATCH (d:Document)-[r]->(s:DocumentSection)
    WHERE type(r) <> 'HAS_SECTION'
    RETURN id(d) AS d_id, id(s) AS s_id, type(r) AS type
    """
    res = session.run(query_d2s)
    for r in res:
        # Save as (s_id, d_id, type) for uniform handling
        sec_to_doc.append((r["s_id"], r["d_id"], r["type"]))

    print(f"Fetched {len(sec_to_doc)} Section-to-Document relationships.")

    return sections, documents, sec_to_sec, sec_to_doc

def build_components(sections, sec_to_sec):
    print("Building DocumentSection connected components...")
    adj = defaultdict(list)
    for id1, id2, _ in sec_to_sec:
        adj[id1].append(id2)
        adj[id2].append(id1)

    visited = set()
    components = []

    for s_id in sections:
        if s_id not in visited:
            comp = []
            queue = deque([s_id])
            visited.add(s_id)
            while queue:
                curr = queue.popleft()
                comp.append(curr)
                for neighbor in adj[curr]:
                    if neighbor not in visited:
                        visited.add(neighbor)
                        queue.append(neighbor)
            components.append(comp)

    print(f"Created {len(components)} connected components of DocumentSections.")
    return components

def run_selection(sections, documents, components, sec_to_sec, sec_to_doc, target_bytes):
    print("Analyzing components and preparing greedy selection...")
    
    # Map section ID to component index
    section_to_comp_idx = {}
    for idx, comp in enumerate(components):
        for s_id in comp:
            section_to_comp_idx[s_id] = idx

    # Initialize component metrics
    comp_neighbor_docs = [set() for _ in range(len(components))]
    comp_has_citation = [False for _ in range(len(components))]
    comp_citation_count = [0 for _ in range(len(components))]
    comp_rels = [[] for _ in range(len(components))]

    for s_id, d_id, rel_type in sec_to_doc:
        c_idx = section_to_comp_idx[s_id]
        comp_neighbor_docs[c_idx].add(d_id)
        comp_rels[c_idx].append((s_id, d_id, rel_type))
        if rel_type != "HAS_SECTION":
            comp_has_citation[c_idx] = True
            comp_citation_count[c_idx] += 1

    for s1, s2, rel_type in sec_to_sec:
        c_idx = section_to_comp_idx[s1]
        comp_rels[c_idx].append((s1, s2, rel_type))
        comp_has_citation[c_idx] = True
        comp_citation_count[c_idx] += 1

    # Format components for sorting
    component_details = []
    for idx, comp in enumerate(components):
        # Estimate section sizes
        est_sec_sz = sum((sections[s][1] * 1.1) + 600 for s in comp)
        component_details.append({
            "index": idx,
            "sections": comp,
            "neighbor_docs": comp_neighbor_docs[idx],
            "has_citation": comp_has_citation[idx],
            "citation_count": comp_citation_count[idx],
            "est_sections_size": est_sec_sz,
            "relationships": comp_rels[idx]
        })

    # Sort components:
    # 1. Core components (has_citation=True) first
    # 2. Most citations first
    # 3. Smaller section size first to maximize coverage
    component_details.sort(key=lambda x: (x["has_citation"], x["citation_count"], -x["est_sections_size"]), reverse=True)

    S_selected = set()
    D_selected = set()
    current_size = 0

    print("Selecting components...")
    for comp in component_details:
        new_sections = [s for s in comp["sections"] if s not in S_selected]
        new_docs = [d for d in comp["neighbor_docs"] if d not in D_selected]
        
        # Estimate increment
        sz_sec = sum((sections[s][1] * 1.1) + 600 for s in new_sections)
        sz_doc = sum((documents[d][1] * 1.3) + 400 for d in new_docs)
        sz_rel = len(comp["relationships"]) * 250
        
        sz_inc = sz_sec + sz_doc + sz_rel
        
        if current_size + sz_inc <= target_bytes:
            S_selected.update(new_sections)
            D_selected.update(new_docs)
            current_size += sz_inc
        else:
            # Skip this component to see if smaller ones fit
            continue

    print(f"Selected {len(S_selected)} DocumentSections and {len(D_selected)} Documents.")
    print(f"Estimated size before document-to-document relationships: {current_size / (1024 * 1024):.2f} MB")
    return S_selected, D_selected, current_size

def fetch_nodes_in_batches(session, node_ids, batch_size=2000):
    node_ids_list = list(node_ids)
    for i in range(0, len(node_ids_list), batch_size):
        batch_ids = node_ids_list[i:i+batch_size]
        query = """
        MATCH (n)
        WHERE id(n) IN $ids
        RETURN id(n) AS id, labels(n) AS labels, properties(n) AS props
        """
        res = session.run(query, ids=batch_ids)
        for r in res:
            yield {
                "id": r["id"],
                "labels": list(r["labels"]),
                "properties": r["props"]
            }

def fetch_section_rels_in_batches(session, section_ids, allowed_ids, seen_rels, batch_size=2000):
    section_ids_list = list(section_ids)
    for i in range(0, len(section_ids_list), batch_size):
        batch_ids = section_ids_list[i:i+batch_size]
        query = """
        MATCH (s:DocumentSection)-[r]-(n)
        WHERE id(s) IN $ids
        RETURN id(s) AS s_id, id(n) AS n_id, type(r) AS type, id(startNode(r)) AS start_id, properties(r) AS props
        """
        res = session.run(query, ids=batch_ids)
        for r in res:
            s_id = r["s_id"]
            n_id = r["n_id"]
            rel_type = r["type"]
            start_id = r["start_id"]
            props = r["props"]
            
            if rel_type in ("PUBLISHED_IN", "HAS_DESCRIPTOR"):
                continue
                
            if n_id not in allowed_ids:
                continue
                
            source = start_id
            target = n_id if start_id == s_id else s_id
            
            rel_key = (source, target, rel_type)
            if rel_key not in seen_rels:
                seen_rels.add(rel_key)
                yield {
                    "source": source,
                    "target": target,
                    "type": rel_type,
                    "properties": props
                }

def fetch_doc_doc_rels(session, doc_ids, seen_rels):
    query = """
    MATCH (d1:Document)-[r]->(d2:Document)
    RETURN id(d1) AS source, id(d2) AS target, type(r) AS type, properties(r) AS props
    """
    res = session.run(query)
    for r in res:
        source = r["source"]
        target = r["target"]
        rel_type = r["type"]
        props = r["props"]
        
        if rel_type in ("PUBLISHED_IN", "HAS_DESCRIPTOR"):
            continue
            
        if source in doc_ids and target in doc_ids:
            rel_key = (source, target, rel_type)
            if rel_key not in seen_rels:
                seen_rels.add(rel_key)
                yield {
                    "source": source,
                    "target": target,
                    "type": rel_type,
                    "properties": props
                }

def main():
    parser = argparse.ArgumentParser(description="Extract custom citation-rich subgraph under memory constraints")
    parser.add_argument("--uri", type=str, default="bolt://localhost:23010", help="Neo4j Bolt URI")
    parser.add_argument("--user", type=str, default="neo4j", help="Neo4j username")
    parser.add_argument("--password", type=str, default="mineGraphRule", help="Neo4j password")
    parser.add_argument("--target-size-mb", type=int, default=950, help="Target output size in MB (default: 950)")
    parser.add_argument("--output", type=str, default="data/extracted_subgraph_custom.json", help="Output JSON path")
    args = parser.parse_args()

    script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    output_filepath = os.path.join(script_dir, args.output)

    print(f"Connecting to database at {args.uri}...")
    try:
        driver = GraphDatabase.driver(args.uri, auth=(args.user, args.password))
        driver.verify_connectivity()
    except Exception as e:
        print(f"Error connecting to database: {e}")
        sys.exit(1)

    target_bytes = args.target_size_mb * 1024 * 1024

    with driver.session() as session:
        # Phase 1: Fetch topology
        sections, documents, sec_to_sec, sec_to_doc = fetch_graph_metadata(session)
        
        # Phase 2: Compute components
        components = build_components(sections, sec_to_sec)
        
        # Phase 3: Selection
        S_selected, D_selected, est_size = run_selection(
            sections, documents, components, sec_to_sec, sec_to_doc, target_bytes
        )

        # Phase 4: Streaming write
        print(f"Streaming subgraph to {output_filepath}...")
        os.makedirs(os.path.dirname(output_filepath), exist_ok=True)
        
        total_nodes_written = 0
        total_rels_written = 0
        
        with open(output_filepath, "w", encoding="utf-8") as f:
            f.write('{\n  "nodes": [\n')
            
            # Write sections
            first_node = True
            for node in fetch_nodes_in_batches(session, S_selected):
                if not first_node:
                    f.write(",\n")
                else:
                    first_node = False
                json.dump(node, f, ensure_ascii=False)
                total_nodes_written += 1
                
            # Write documents
            for node in fetch_nodes_in_batches(session, D_selected):
                if not first_node:
                    f.write(",\n")
                else:
                    first_node = False
                json.dump(node, f, ensure_ascii=False)
                total_nodes_written += 1
                
            f.write('\n  ],\n  "relationships": [\n')
            
            # Write relationships
            first_rel = True
            seen_rels = set()
            allowed_ids = S_selected | D_selected
            
            for rel in fetch_section_rels_in_batches(session, S_selected, allowed_ids, seen_rels):
                if not first_rel:
                    f.write(",\n")
                else:
                    first_rel = False
                json.dump(rel, f, ensure_ascii=False)
                total_rels_written += 1
                
            for rel in fetch_doc_doc_rels(session, D_selected, seen_rels):
                if not first_rel:
                    f.write(",\n")
                else:
                    first_rel = False
                json.dump(rel, f, ensure_ascii=False)
                total_rels_written += 1
                
            f.write('\n  ]\n}\n')

        file_size_mb = os.path.getsize(output_filepath) / (1024 * 1024)
        print("=" * 60)
        print("EXTRACTION COMPLETE!")
        print(f"Nodes written: {total_nodes_written}")
        print(f"Relationships written: {total_rels_written}")
        print(f"Final output file size: {file_size_mb:.2f} MB")
        print("=" * 60)

    driver.close()

if __name__ == "__main__":
    main()
