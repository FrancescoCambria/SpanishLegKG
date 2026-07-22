import os
import sys
import json
import argparse
from collections import deque
from neo4j import GraphDatabase

def get_representative_seed_nodes(session):
    print("Collecting seed nodes to guarantee representation of all labels and relationship types...")
    seed_nodes = set()

    # 1. Fetch one node ID for each unique label in the database
    query_labels = """
    MATCH (n)
    WITH labels(n) AS lbls, id(n) AS node_id
    UNWIND lbls AS label
    RETURN label, min(node_id) AS rep_id
    """
    try:
        res = session.run(query_labels)
        for record in res:
            if record["rep_id"] is not None:
                seed_nodes.add(record["rep_id"])
    except Exception as e:
        print(f"Warning: failed to query label representatives: {e}")

    # 2. Fetch one source and target node ID for each unique relationship type in the database
    query_rels = """
    MATCH (n1)-[r]->(n2)
    WITH type(r) AS rel_type, id(n1) AS s_id, id(n2) AS t_id
    RETURN rel_type, head(collect({s: s_id, t: t_id})) AS edge
    """
    try:
        res = session.run(query_rels)
        for record in res:
            edge = record["edge"]
            if edge:
                seed_nodes.add(edge["s"])
                seed_nodes.add(edge["t"])
    except Exception as e:
        print(f"Warning: failed to query relationship representatives: {e}")

    print(f"Collected {len(seed_nodes)} initial seed nodes.")
    return list(seed_nodes)

def fetch_nodes_details(session, node_ids):
    query = """
    MATCH (n)
    WHERE id(n) IN $ids
    RETURN id(n) AS node_id, labels(n) AS labels, properties(n) AS props
    """
    result = session.run(query, ids=node_ids)
    nodes = []
    for record in result:
        nodes.append({
            "id": record["node_id"],
            "labels": list(record["labels"]),
            "properties": record["props"]
        })
    return nodes

def fetch_relationships(session, node_ids):
    query = """
    MATCH (n1)-[r]->(n2)
    WHERE id(n1) IN $ids AND id(n2) IN $ids
    RETURN id(n1) AS source, id(n2) AS target, type(r) AS type, properties(r) AS props
    """
    result = session.run(query, ids=node_ids)
    relationships = []
    for record in result:
        relationships.append({
            "source": record["source"],
            "target": record["target"],
            "type": record["type"],
            "properties": record["props"]
        })
    return relationships

def main():
    parser = argparse.ArgumentParser(description="Extract a 1GB connected subgraph with high density of DocumentSections")
    parser.add_argument("--uri", type=str, default="bolt://localhost:23010", help="Neo4j/Memgraph Bolt URI")
    parser.add_argument("--user", type=str, default="neo4j", help="Neo4j username")
    parser.add_argument("--password", type=str, default="mineGraphRule", help="Neo4j password")
    parser.add_argument("--target-size-mb", type=int, default=950, help="Target size in MB (default: 950)")
    parser.add_argument("--max-nodes", type=int, default=50000, help="Safety limit for maximum nodes (default: 50000)")
    parser.add_argument("--output", type=str, default="data/extracted_subgraph_large.json", help="Output JSON file name")
    args = parser.parse_args()

    # Resolve paths relative to the parent Catalonia root directory
    script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    output_filepath = os.path.join(script_dir, args.output)

    print(f"Connecting to database at {args.uri}...")
    try:
        driver = GraphDatabase.driver(args.uri, auth=(args.user, args.password))
        driver.verify_connectivity()
    except Exception as e:
        print(f"Error connecting to database: {e}")
        sys.exit(1)

    with driver.session() as session:
        seed_nodes = get_representative_seed_nodes(session)
        if not seed_nodes:
            print("Error: No nodes found in the database.")
            driver.close()
            sys.exit(1)

        visited = set(seed_nodes)
        queue = deque(seed_nodes)
        
        target_bytes = args.target_size_mb * 1024 * 1024
        current_size_bytes = 0
        batch_count = 0
        
        print("Starting BFS traversal (prioritizing DocumentSection nodes)...")
        
        # We will loop and grow the visited set, checking size incrementally
        while queue and current_size_bytes < target_bytes and len(visited) < args.max_nodes:
            # 1. Expand BFS queue in batches
            current_batch = []
            for _ in range(min(len(queue), 200)):
                if queue:
                    current_batch.append(queue.popleft())
            
            if not current_batch:
                break
                
            # Get neighbors and their labels
            query = """
            MATCH (n)-[]-(neighbor)
            WHERE id(n) IN $ids
            RETURN DISTINCT id(neighbor) AS neighbor_id, labels(neighbor) AS labels
            """
            result = session.run(query, ids=current_batch)
            
            sections = []
            others = []
            
            for record in result:
                n_id = record["neighbor_id"]
                labels = record["labels"]
                if n_id not in visited:
                    if "DocumentSection" in labels:
                        sections.append(n_id)
                    else:
                        others.append(n_id)
            
            # Prioritize adding sections first
            for s_id in sections:
                if len(visited) < args.max_nodes:
                    visited.add(s_id)
                    queue.append(s_id)
                    
            for o_id in others:
                if len(visited) < args.max_nodes:
                    visited.add(o_id)
                    queue.append(o_id)
                    
            batch_count += 1
            
            # 2. Incrementally check subgraph size every 15 batches (approx. 2000-3000 nodes added)
            if batch_count % 15 == 0 or len(visited) >= args.max_nodes:
                print(f"Evaluating subgraph size... Current nodes collected: {len(visited)}")
                # Fetch details of current nodes and relationships
                nodes_list = list(visited)
                nodes_details = fetch_nodes_details(session, nodes_list)
                relationships = fetch_relationships(session, nodes_list)
                
                subgraph = {
                    "nodes": nodes_details,
                    "relationships": relationships
                }
                
                # Estimate size in memory
                serialized = json.dumps(subgraph, ensure_ascii=False)
                current_size_bytes = len(serialized.encode('utf-8'))
                size_mb = current_size_bytes / (1024 * 1024)
                
                section_count = sum(1 for n in nodes_details if "DocumentSection" in n["labels"])
                print(f"  -> Size estimate: {size_mb:.2f} MB ({len(nodes_details)} nodes, {len(relationships)} relationships)")
                print(f"  -> DocumentSection nodes density: {section_count}/{len(nodes_details)} ({section_count/len(nodes_details)*100:.2f}%)")
                
                if current_size_bytes >= target_bytes:
                    print("Target size reached!")
                    break
        
        # 3. Final Fetch and Save
        print("Finalizing subgraph extraction...")
        nodes_list = list(visited)
        nodes_details = fetch_nodes_details(session, nodes_list)
        relationships = fetch_relationships(session, nodes_list)
        
        subgraph = {
            "nodes": nodes_details,
            "relationships": relationships
        }
        
        print(f"Saving final subgraph to {output_filepath}...")
        with open(output_filepath, "w", encoding="utf-8") as f:
            json.dump(subgraph, f, indent=2, ensure_ascii=False)
            
        file_size_mb = os.path.getsize(output_filepath) / (1024 * 1024)
        section_count = sum(1 for n in nodes_details if "DocumentSection" in n["labels"])
        print(f"Extraction complete! Saved {len(nodes_details)} nodes and {len(relationships)} relationships.")
        print(f"Final File size: {file_size_mb:.2f} MB")
        print(f"Final DocumentSection count: {section_count} ({section_count/len(nodes_details)*100:.2f}%)")

    driver.close()

if __name__ == "__main__":
    main()
