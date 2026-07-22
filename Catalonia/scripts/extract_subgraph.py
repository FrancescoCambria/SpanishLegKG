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

def bfs_traverse(session, seed_node_ids, target_count):
    print(f"Starting BFS traversal from seeds to collect {target_count} nodes...")
    visited = set(seed_node_ids)
    queue = deque(seed_node_ids)
    
    while queue and len(visited) < target_count:
        current_batch = []
        # Process in batches to reduce database round-trips
        for _ in range(min(len(queue), 200)):
            if queue:
                current_batch.append(queue.popleft())
                
        if not current_batch:
            break
            
        # Get all neighbors for the current batch of nodes
        query = """
        MATCH (n)-[]-(neighbor)
        WHERE id(n) IN $ids
        RETURN DISTINCT id(neighbor) AS neighbor_id
        """
        result = session.run(query, ids=current_batch)
        for record in result:
            neighbor_id = record["neighbor_id"]
            if neighbor_id not in visited:
                visited.add(neighbor_id)
                queue.append(neighbor_id)
                if len(visited) >= target_count:
                    break
                    
    return list(visited)

def fetch_nodes_details(session, node_ids):
    print(f"Fetching details for {len(node_ids)} nodes...")
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
    print("Fetching relationships among the selected nodes...")
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
    parser = argparse.ArgumentParser(description="Extract a connected subgraph from Neo4j/Memgraph with guaranteed label coverage")
    parser.add_argument("--uri", type=str, default="bolt://localhost:23010", help="Neo4j/Memgraph Bolt URI")
    parser.add_argument("--user", type=str, default="neo4j", help="Neo4j username")
    parser.add_argument("--password", type=str, default="mineGraphRule", help="Neo4j password")
    parser.add_argument("--limit", type=int, default=5000, help="Number of nodes to extract")
    parser.add_argument("--output", type=str, default="data/extracted_subgraph.json", help="Output JSON file name")
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
        
        node_ids = bfs_traverse(session, seed_nodes, args.limit)
        print(f"Successfully collected {len(node_ids)} connected nodes.")
        
        nodes_details = fetch_nodes_details(session, node_ids)
        relationships = fetch_relationships(session, node_ids)
        
        subgraph = {
            "nodes": nodes_details,
            "relationships": relationships
        }
        
        print(f"Saving subgraph to {output_filepath}...")
        with open(output_filepath, "w", encoding="utf-8") as f:
            json.dump(subgraph, f, indent=2, ensure_ascii=False)
            
        file_size_mb = os.path.getsize(output_filepath) / (1024 * 1024)
        print(f"Extraction complete! Saved {len(nodes_details)} nodes and {len(relationships)} relationships.")
        print(f"File size: {file_size_mb:.2f} MB")

    driver.close()

if __name__ == "__main__":
    main()
