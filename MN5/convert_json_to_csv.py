import json
import csv
import sys
from pathlib import Path

def convert_json_to_csv(json_path, output_dir="import_csvs"):
    json_path = Path(json_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(exist_ok=True)
    
    print(f"Loading JSON from {json_path} (this may take a few moments for 750MB)...")
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
        
    nodes = data.get("nodes", [])
    relationships = data.get("relationships", [])
    
    print(f"Loaded {len(nodes)} nodes and {len(relationships)} relationships.")
    
    # 1. Scan all nodes to find all unique property keys
    print("Scanning node property keys...")
    node_properties = set()
    for node in nodes:
        node_properties.update(node.get("properties", {}).keys())
    
    # 2. Scan all relationships to find all unique property keys
    print("Scanning relationship property keys...")
    rel_properties = set()
    for rel in relationships:
        rel_properties.update(rel.get("properties", {}).keys())
        
    node_properties_list = sorted(list(node_properties))
    rel_properties_list = sorted(list(rel_properties))
    
    # 3. Write Nodes CSV
    nodes_csv_path = output_dir / "nodes.csv"
    print(f"Writing nodes to {nodes_csv_path}...")
    
    node_headers = ["id:ID", ":LABEL"] + node_properties_list
    with open(nodes_csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
        writer.writerow(node_headers)
        
        for node in nodes:
            node_id = node.get("id")
            labels = ";".join(node.get("labels", [])) # Neo4j uses ';' as array separator
            properties = node.get("properties", {})
            
            row = [node_id, labels]
            for prop in node_properties_list:
                val = properties.get(prop, "")
                # Convert lists/dicts to string representations if they exist
                if isinstance(val, (list, dict)):
                    val = json.dumps(val)
                row.append(val)
            writer.writerow(row)
            
    # 4. Write Relationships CSV
    rels_csv_path = output_dir / "relationships.csv"
    print(f"Writing relationships to {rels_csv_path}...")
    
    rel_headers = [":START_ID", ":END_ID", ":TYPE"] + rel_properties_list
    with open(rels_csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
        writer.writerow(rel_headers)
        
        for rel in relationships:
            source = rel.get("source")
            target = rel.get("target")
            rel_type = rel.get("type")
            properties = rel.get("properties", {})
            
            row = [source, target, rel_type]
            for prop in rel_properties_list:
                val = properties.get(prop, "")
                if isinstance(val, (list, dict)):
                    val = json.dumps(val)
                row.append(val)
            writer.writerow(row)
            
    print("\nConversion completed successfully!")
    print(f"Nodes CSV: {nodes_csv_path.resolve()}")
    print(f"Relationships CSV: {rels_csv_path.resolve()}")
    print("\nNext steps command suggestion:")
    print(f"neo4j-admin database import full --nodes={nodes_csv_path.name} --relationships={rels_csv_path.name} --multiline-fields=true neo4j")

if __name__ == "__main__":
    input_file = "extracted_subgraph_large.json"
    if len(sys.argv) > 1:
        input_file = sys.argv[1]
    convert_json_to_csv(input_file)
