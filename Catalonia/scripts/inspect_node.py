#!/usr/bin/env python3
"""
inspect_node.py - Node Inspection & Markdown Report Generator for Catalonia Law Graph

Usage:
    python3 pipeline_scripts/inspect_node.py <node_id> [graph_json_path] [--output-md path.md] [--output-json path.json]

Example:
    python3 pipeline_scripts/inspect_node.py 1483 data/extracted_subgraph_custom_updated_v4.json
"""

import os
import sys
import json
import argparse
from pathlib import Path

def get_default_graph_path(cat_root):
    candidates = [
        os.path.join(cat_root, "data", "extracted_subgraph_custom_updated_v4.json"),
        os.path.join(cat_root, "data", "extracted_subgraph_custom_updated.json"),
        os.path.join(cat_root, "data", "extracted_subgraph_custom.json"),
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return candidates[0]

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

    # Map parent document for section/article nodes via HAS_SECTION or HAS_DOCUMENT
    parent_doc_by_section_id = {}
    for r in rels:
        r_type = r.get("type") or ""
        if r_type in ["HAS_SECTION", "HAS_DOCUMENT", "PART_OF"]:
            src_id = r.get("source")
            sec_id = r.get("target")
            p_node = nodes_by_id.get(src_id) or nodes_by_id.get(str(src_id))
            if p_node:
                parent_doc_by_section_id[sec_id] = p_node
                parent_doc_by_section_id[str(sec_id)] = p_node

    def _get_parent_doc_info(nid):
        p_node = parent_doc_by_section_id.get(nid) or parent_doc_by_section_id.get(str(nid))
        if p_node:
            p_props = p_node.get("properties", {}) or {}
            p_title = p_props.get("titleCa") or p_props.get("titleEs") or p_props.get("title") or f"Document {p_node['id']}"
            return {"parent_id": p_node["id"], "parent_title": p_title, "parent_labels": p_node.get("labels", [])}
        return None

    # Target node parent document info
    target_parent_info = _get_parent_doc_info(target_id)

    # Process all incoming & outgoing relationships
    incoming_rels = []
    outgoing_rels = []

    # Categorize relationships into groups
    citation_types = {"CITES", "CITED_BY", "CITES_LAW", "CITES_SECTION"}
    affectation_types = {"AFFECTS", "AFFECTED_BY", "MODIFIES", "REPEALS", "DEROGATES", "AMENDS", "MODIFIED_BY", "REPEALED_BY"}
    parent_law_types = {"HAS_SECTION", "HAS_DOCUMENT", "PART_OF", "PUBLISHED_IN"}
    descriptor_types = {"HAS_DESCRIPTOR"}

    for r in rels:
        r_type = r.get("type", "UNKNOWN")
        r_props = r.get("properties", {}) or {}
        
        # Check incoming
        if r.get("target") == target_id or str(r.get("target")) == str(target_id):
            src_id = r.get("source")
            src_node = nodes_by_id.get(src_id) or nodes_by_id.get(str(src_id)) or {}
            src_props = src_node.get("properties", {}) if src_node else {}
            src_title = src_props.get("titleCa") or src_props.get("titleEs") or src_props.get("title") or f"Node {src_id}"

            rel_item = {
                "source_id": src_id,
                "source_title": src_title,
                "source_labels": src_node.get("labels", []) if src_node else [],
                "type": r_type,
                "properties": r_props,
                "parent_document": _get_parent_doc_info(src_id)
            }
            incoming_rels.append(rel_item)

        # Check outgoing
        if r.get("source") == target_id or str(r.get("source")) == str(target_id):
            tgt_id = r.get("target")
            tgt_node = nodes_by_id.get(tgt_id) or nodes_by_id.get(str(tgt_id)) or {}
            tgt_props = tgt_node.get("properties", {}) if tgt_node else {}
            tgt_title = tgt_props.get("titleCa") or tgt_props.get("titleEs") or tgt_props.get("title") or f"Node {tgt_id}"

            rel_item = {
                "target_id": tgt_id,
                "target_title": tgt_title,
                "target_labels": tgt_node.get("labels", []) if tgt_node else [],
                "type": r_type,
                "properties": r_props,
                "parent_document": _get_parent_doc_info(tgt_id)
            }
            outgoing_rels.append(rel_item)

    node_title = props.get("titleCa") or props.get("titleEs") or props.get("title") or f"Node {target_id}"

    # Default output MD file if not specified
    if not output_md:
        output_md = f"node_{target_id}_report.md"

    # Build Markdown Report
    md = []
    md.append(f"# Node Inspection Report: ID `{target_id}`\n")
    md.append(f"- **Title**: {node_title}")
    md.append(f"- **Labels**: `{':'.join(labels)}`")
    if target_parent_info:
        md.append(f"- **Parent Law / Document**: Node `{target_parent_info['parent_id']}` (*{target_parent_info['parent_title']}*)")
    md.append(f"- **Document Number**: `{props.get('documentNumber') or 'N/A'}`")
    md.append(f"- **ELI URI**: `{props.get('eliUri') or 'N/A'}`")
    md.append(f"- **Section / Type**: `{props.get('section') or props.get('typeOfLaw') or props.get('type') or 'N/A'}`")
    md.append("")

    # Full text section
    md.append("## Node Text Content\n")
    md.append("> " + full_text.replace("\n", "\n> ") + "\n")

    # Parent Law / Structural Hierarchy
    md.append("## Parent Law & Structural Hierarchy\n")
    if target_parent_info:
        md.append(f"- **Parent Document Node**: `{target_parent_info['parent_id']}`")
        md.append(f"- **Parent Title**: {target_parent_info['parent_title']}")
        md.append(f"- **Parent Labels**: `{':'.join(target_parent_info['parent_labels'])}`")
    else:
        md.append("- *This node is a root document or has no structural parent link.*")
    md.append("")

    # Relationships summary table
    md.append(f"## Graph Relationships Summary ({len(incoming_rels)} Incoming | {len(outgoing_rels)} Outgoing)\n")

    # Categorized Outgoing Relationships
    out_citations = [r for r in outgoing_rels if r["type"] in citation_types]
    out_affectations = [r for r in outgoing_rels if r["type"] in affectation_types]
    out_parent_law = [r for r in outgoing_rels if r["type"] in parent_law_types]
    out_descriptors = [r for r in outgoing_rels if r["type"] in descriptor_types]
    out_other = [r for r in outgoing_rels if r["type"] not in citation_types | affectation_types | parent_law_types | descriptor_types]

    md.append("### Outgoing Edges\n")
    
    if out_parent_law:
        md.append("#### Structural / Parent Law Edges")
        for r in out_parent_law:
            md.append(f"- `{r['type']}` ➔ Target Node `{r['target_id']}` (*{r['target_title']}*)")
        md.append("")

    if out_affectations:
        md.append("#### Affectations (`afectacions` - Legal Modifications)")
        for r in out_affectations:
            rprops = r["properties"]
            md.append(f"- `{r['type']}` ➔ Target Node `{r['target_id']}` (*{r['target_title']}*)")
            if rprops.get("details"):
                md.append(f"  - **Details**: `{rprops['details']}`")
            if rprops.get("text"):
                md.append(f"  - **Modification Text**: \"{rprops['text']}\"")
        md.append("")

    if out_citations:
        md.append("#### Citation Edges (`CITES`)")
        for r in out_citations:
            rprops = r["properties"]
            md.append(f"- `{r['type']}` ➔ Target Node `{r['target_id']}` (*{r['target_title']}*)")
            if rprops.get("details"):
                md.append(f"  - **Details**: `{rprops['details']}`")
            if rprops.get("cited_text"):
                md.append(f"  - **Cited Text**: \"{rprops['cited_text']}\"")
            if rprops.get("extracted_by"):
                md.append(f"  - **Extracted By**: `{rprops['extracted_by']}`")
        md.append("")

    if out_descriptors:
        md.append("#### Descriptors (`HAS_DESCRIPTOR`)")
        for r in out_descriptors:
            md.append(f"- `HAS_DESCRIPTOR` ➔ Descriptor Node `{r['target_id']}` (*{r['target_title']}*)")
        md.append("")

    if out_other:
        md.append("#### Other Outgoing Edges")
        for r in out_other:
            md.append(f"- `{r['type']}` ➔ Target Node `{r['target_id']}` (*{r['target_title']}*)")
        md.append("")

    if not outgoing_rels:
        md.append("- *(No outgoing relationships)*\n")

    # Categorized Incoming Relationships
    in_citations = [r for r in incoming_rels if r["type"] in citation_types]
    in_affectations = [r for r in incoming_rels if r["type"] in affectation_types]
    in_parent_law = [r for r in incoming_rels if r["type"] in parent_law_types]
    in_other = [r for r in incoming_rels if r["type"] not in citation_types | affectation_types | parent_law_types]

    md.append("### Incoming Edges\n")

    if in_parent_law:
        md.append("#### Structural / Parent Law Edges")
        for r in in_parent_law:
            md.append(f"- `{r['type']}` ⬅ FROM Source Node `{r['source_id']}` (*{r['source_title']}*)")
        md.append("")

    if in_affectations:
        md.append("#### Incoming Affectations (Modified / Affected By)")
        for r in in_affectations:
            rprops = r["properties"]
            md.append(f"- `{r['type']}` ⬅ FROM Source Node `{r['source_id']}` (*{r['source_title']}*)")
            if rprops.get("details"):
                md.append(f"  - **Details**: `{rprops['details']}`")
        md.append("")

    if in_citations:
        md.append("#### Incoming Citations (`CITED_BY`)")
        for r in in_citations:
            md.append(f"- `{r['type']}` ⬅ FROM Source Node `{r['source_id']}` (*{r['source_title']}*)")
        md.append("")

    if in_other:
        md.append("#### Other Incoming Edges")
        for r in in_other:
            md.append(f"- `{r['type']}` ⬅ FROM Source Node `{r['source_id']}` (*{r['source_title']}*)")
        md.append("")

    if not incoming_rels:
        md.append("- *(No incoming relationships)*\n")

    # Node Properties Json
    md.append("## Full Node Properties Metadata\n```json\n" + json.dumps(props, indent=2, ensure_ascii=False) + "\n```\n")

    md_content = "\n".join(md)

    with open(output_md, "w", encoding="utf-8") as f:
        f.write(md_content)

    print(f"\nSuccessfully generated Markdown report for Node `{target_id}`: {output_md}")

    if output_json:
        result_json = {
            "node_id": target_id,
            "labels": labels,
            "properties": props,
            "parent_document": target_parent_info,
            "full_text": full_text,
            "incoming_relationships": incoming_rels,
            "outgoing_relationships": outgoing_rels
        }
        with open(output_json, "w", encoding="utf-8") as f:
            json.dump(result_json, f, indent=2, ensure_ascii=False)
        print(f"Successfully generated JSON report for Node `{target_id}`: {output_json}")

    return output_md

def main():
    cat_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    default_graph = get_default_graph_path(cat_root)

    parser = argparse.ArgumentParser(description="Inspect a specific graph node (text, edges, parent law, affectations, descriptors) and create a markdown report.")
    parser.add_argument("node_id", help="ID of the node to inspect (e.g. 1483 or 182489)")
    parser.add_argument("graph_json", nargs="?", default=default_graph, help="Path to input graph JSON file")
    parser.add_argument("--output-md", default=None, help="Path to export Markdown report (defaults to node_<id>_report.md)")
    parser.add_argument("--output-json", default=None, help="Optional path to export raw JSON report")

    args = parser.parse_args()

    target_id = int(args.node_id) if str(args.node_id).isdigit() else args.node_id
    inspect_node(target_id, args.graph_json, args.output_md, args.output_json)

if __name__ == "__main__":
    main()
