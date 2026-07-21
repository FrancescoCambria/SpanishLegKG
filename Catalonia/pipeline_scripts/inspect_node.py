#!/usr/bin/env python3
import os
import sys
import json
import argparse
from pathlib import Path

def inspect_node(node_id, graph_json_path, output_md=None, output_json=None):
    graph_path = Path(graph_json_path)
    if not graph_path.exists():
        print(f"Error: Graph file not found at '{graph_json_path}'", file=sys.stderr)
        sys.exit(1)
        
    print(f"Loading graph data from {graph_path}...")
    with open(graph_path, "r", encoding="utf-8") as f:
        graph_data = json.load(f)
        
    nodes = graph_data.get("nodes", [])
    rels = graph_data.get("relationships", [])
    
    # Build node lookup table
    nodes_by_id = {}
    for n in nodes:
        nid = n["id"]
        nodes_by_id[nid] = n
        nodes_by_id[str(nid)] = n

    target_node = nodes_by_id.get(node_id) or nodes_by_id.get(str(node_id))
    if not target_node:
        print(f"Error: Node ID '{node_id}' not found in graph database.", file=sys.stderr)
        sys.exit(1)

    target_id = target_node["id"]
    labels = target_node.get("labels", [])
    props = target_node.get("properties", {}) or {}

    # Extract text content
    text_es = props.get("textEs") or ""
    text_ca = props.get("textCa") or ""
    full_text = text_es or text_ca or props.get("text") or "(No text content present on this node)"

    # Incoming relationships (edges where target == target_id)
    incoming_rels = []
    for r in rels:
        if r.get("target") == target_id or str(r.get("target")) == str(target_id):
            src_id = r.get("source")
            src_node = nodes_by_id.get(src_id) or {}
            src_props = src_node.get("properties", {}) if src_node else {}
            src_title = src_props.get("titleEs") or src_props.get("titleCa") or src_props.get("title") or f"Node {src_id}"
            
            incoming_rels.append({
                "source_id": src_id,
                "source_title": src_title,
                "source_labels": src_node.get("labels", []),
                "type": r.get("type"),
                "properties": r.get("properties", {}) or {}
            })

    # Outgoing relationships (edges where source == target_id)
    outgoing_rels = []
    for r in rels:
        if r.get("source") == target_id or str(r.get("source")) == str(target_id):
            tgt_id = r.get("target")
            tgt_node = nodes_by_id.get(tgt_id) or {}
            tgt_props = tgt_node.get("properties", {}) if tgt_node else {}
            tgt_title = tgt_props.get("titleEs") or tgt_props.get("titleCa") or tgt_props.get("title") or f"Node {tgt_id}"
            
            outgoing_rels.append({
                "target_id": tgt_id,
                "target_title": tgt_title,
                "target_labels": tgt_node.get("labels", []),
                "type": r.get("type"),
                "properties": r.get("properties", {}) or {}
            })

    node_inspection_result = {
        "node_id": target_id,
        "labels": labels,
        "properties": props,
        "full_text": full_text,
        "incoming_relationships_count": len(incoming_rels),
        "incoming_relationships": incoming_rels,
        "outgoing_relationships_count": len(outgoing_rels),
        "outgoing_relationships": outgoing_rels
    }

    # Print clean formatted summary to stdout
    print("\n" + "=" * 80)
    print(f" NODE INSPECTION REPORT: ID {target_id}")
    print("=" * 80)
    print(f"Labels: {':'.join(labels)}")
    print(f"Title: {props.get('titleEs') or props.get('titleCa') or props.get('title') or 'N/A'}")
    print(f"Document Number: {props.get('documentNumber') or 'N/A'}")
    print(f"ELI URI: {props.get('eliUri') or 'N/A'}")
    print(f"Section / Type: {props.get('section') or props.get('typeOfLaw') or props.get('type') or 'N/A'}")
    print(f"Processed by V3: {props.get('processed_by_simple_agent_v3', False)}")
    
    if props.get('processed_by_simple_agent_v3'):
        print(f"V3 Stats: Kept={props.get('v3_citations_kept',0)} | Modified={props.get('v3_citations_modified',0)} | Removed={props.get('v3_citations_removed',0)} | Added={props.get('v3_citations_added',0)}")
        
    print("\n--- NODE PROPERTIES ---")
    for k, v in sorted(props.items()):
        if k not in ["textEs", "textCa", "text"]:
            print(f"  {k}: {v}")

    print("\n--- FULL TEXT ---")
    print(full_text)

    print(f"\n--- INCOMING RELATIONSHIPS ({len(incoming_rels)}) ---")
    if not incoming_rels:
        print("  (No incoming relationships)")
    for i, r in enumerate(incoming_rels, 1):
        rprops = r["properties"]
        print(f"  {i}. [{r['type']}] FROM Source `{r['source_id']}` ({':'.join(r['source_labels'])})")
        print(f"     Title: {r['source_title']}")
        if rprops:
            print(f"     Properties: {rprops}")

    print(f"\n--- OUTGOING RELATIONSHIPS ({len(outgoing_rels)}) ---")
    if not outgoing_rels:
        print("  (No outgoing relationships)")
    for i, r in enumerate(outgoing_rels, 1):
        rprops = r["properties"]
        print(f"  {i}. [{r['type']}] TO Target `{r['target_id']}` ({':'.join(r['target_labels'])})")
        print(f"     Title: {r['target_title']}")
        if rprops.get("details"):
            print(f"     Details: {rprops['details']}")
        if rprops.get("cited_text"):
            print(f"     Cited Text: \"{rprops['cited_text']}\"")
        if rprops.get("extracted_by"):
            print(f"     Extracted By: {rprops['extracted_by']}")
        if rprops.get("verified_and_modified_by_v3"):
            print(f"     Status: VERIFIED & MODIFIED BY V3")
            bstate = rprops.get("v3_original_state")
            if bstate:
                print(f"     Original State Before V3: [{bstate.get('type')}] Target `{bstate.get('target_id')}` ({bstate.get('target_title')}) Details: `{bstate.get('details')}`")
        elif rprops.get("verified_by_v3"):
            print(f"     Status: VERIFIED BY V3 (KEEP)")

    print("=" * 80 + "\n")

    # Save Markdown if requested
    if output_md:
        md_content = f"# Node Inspection Report: ID `{target_id}`\n\n"
        md_content += f"- **Labels**: `{':'.join(labels)}`\n"
        md_content += f"- **Title**: {props.get('titleEs') or props.get('titleCa') or props.get('title') or 'N/A'}\n"
        md_content += f"- **Document Number**: `{props.get('documentNumber') or 'N/A'}`\n"
        md_content += f"- **ELI URI**: `{props.get('eliUri') or 'N/A'}`\n\n"
        
        md_content += "## Node Properties\n```json\n" + json.dumps(props, indent=2, ensure_ascii=False) + "\n```\n\n"
        md_content += "## Full Text Content\n> " + full_text.replace("\n", "\n> ") + "\n\n"
        
        md_content += f"## Incoming Relationships ({len(incoming_rels)})\n"
        for r in incoming_rels:
            md_content += f"- `{r['type']}` FROM Source Node `{r['source_id']}` (*{r['source_title']}*)\n"
            if r["properties"]:
                md_content += f"  - Properties: `{json.dumps(r['properties'], ensure_ascii=False)}`\n"
        md_content += "\n"
        
        md_content += f"## Outgoing Relationships ({len(outgoing_rels)})\n"
        for r in outgoing_rels:
            rprops = r["properties"]
            md_content += f"- `{r['type']}` TO Target Node `{r['target_id']}` (*{r['target_title']}*)\n"
            if rprops.get("details"):
                md_content += f"  - **Details**: `{rprops['details']}`\n"
            if rprops.get("cited_text"):
                md_content += f"  - **Cited Text**: \"{rprops['cited_text']}\"\n"
            if rprops.get("v3_original_state"):
                b = rprops["v3_original_state"]
                md_content += f"  - **Original Before V3**: `{b.get('type')}` TO Target Node `{b.get('target_id')}` (*{b.get('target_title')}*) Details: `{b.get('details')}`\n"
        md_content += "\n"
        
        with open(output_md, "w", encoding="utf-8") as f:
            f.write(md_content)
        print(f"Exported Markdown report to: {output_md}")

    # Save JSON if requested
    if output_json:
        with open(output_json, "w", encoding="utf-8") as f:
            json.dump(node_inspection_result, f, indent=2, ensure_ascii=False)
        print(f"Exported JSON report to: {output_json}")

    return node_inspection_result


def main():
    parser = argparse.ArgumentParser(description="Inspect a specific graph node (full text, properties, incoming and outgoing relationships).")
    parser.add_argument("node_id", help="ID of the node to inspect (e.g. 182489 or 1483)")
    parser.add_argument("graph_json", nargs="?", default="/home/cambria/gram3/LawGraph/Spain/Catalonia/data/extracted_subgraph_custom_updated_v3.json", help="Path to input graph JSON file")
    parser.add_argument("--output-md", default=None, help="Path to export Markdown report")
    parser.add_argument("--output-json", default=None, help="Path to export raw JSON inspection result")

    args = parser.parse_args()
    
    target_id = int(args.node_id) if str(args.node_id).isdigit() else args.node_id
    inspect_node(target_id, args.graph_json, args.output_md, args.output_json)


if __name__ == "__main__":
    main()
