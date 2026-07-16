#!/usr/bin/env python3
"""
BOE Citation Extractor
Processes downloaded BOE XML files to extract citation edges between laws.
Determines the direction: Citing Law & Article -> Cited Law & Article.
Saves the output as a CSV edge list for graph database import or analysis.
"""

import os
import sys
import re
import csv
import json
import glob
import argparse
import multiprocessing
from concurrent.futures import ProcessPoolExecutor, as_completed
import xml.etree.ElementTree as ET

# Regex pattern to match articles/sections/paragraphs in reference texts
ARTICLE_PATTERN = re.compile(
    r"\b(?:art(?:ículos?|ículo|\.)?|apartados?|disposici(?:ón|ones)\s+adicional(?:es)?)\s+([0-9a-zA-ZáéíóúÁÉÍÓÚñÑ\.\(\)\s,y\-]+?)(?=\s+(?:de|del|en|para|por)\b|$)",
    re.IGNORECASE
)

# Regex pattern to find law serial numbers (e.g., 24/2005, 34/1998, 3292/2008)
LAW_NUM_PATTERN = re.compile(r"\b\d+/\d{4}\b")

def parse_xml_file(file_path):
    """
    Parses a single BOE XML file to extract its internal article structure and references.
    """
    try:
        tree = ET.parse(file_path)
        root = tree.getroot()
    except Exception as e:
        return {"error": f"Parse error: {e}", "file": file_path}

    # Extract metadata
    meta = root.find("metadatos")
    if meta is None:
        return {"error": "Missing metadata block", "file": file_path}
        
    boe_id = meta.findtext("identificador")
    title = meta.findtext("titulo")
    
    # Extract structural blocks of the text
    blocks = []
    current_block = "Preámbulo"
    current_text = []
    
    texto_el = root.find("texto")
    if texto_el is not None:
        for p in texto_el.findall("p"):
            p_class = p.get("class")
            p_text = p.text or ""
            p_text = p_text.strip()
            if not p_text:
                continue
                
            if p_class == "articulo":
                # Save previous block
                if current_text:
                    blocks.append({
                        "name": current_block.replace("[precepto]", "").strip(),
                        "text": "\n".join(current_text)
                    })
                current_block = p_text
                current_text = []
            else:
                current_text.append(p_text)
                
        # Save last block
        if current_text:
            blocks.append({
                "name": current_block.replace("[precepto]", "").strip(),
                "text": "\n".join(current_text)
            })

    # Extract references (anteriores and posteriores)
    refs = []
    ref_el = root.find("analisis/referencias")
    if ref_el is not None:
        # Anteriores
        for ant in ref_el.findall("anteriores/anterior"):
            ref_id = ant.get("referencia")
            palabra = ant.findtext("palabra")
            text = ant.findtext("texto")
            if ref_id:
                refs.append({
                    "target_id": ref_id,
                    "relation": palabra or "REFERENCIA",
                    "text": text or ""
                })
        # Posteriores
        for post in ref_el.findall("posteriores/posterior"):
            ref_id = post.get("referencia")
            palabra = post.findtext("palabra")
            text = post.findtext("texto")
            if ref_id:
                refs.append({
                    "target_id": ref_id,
                    "relation": palabra or "REFERENCIA",
                    "text": text or ""
                })

    return {
        "boe_id": boe_id,
        "title": title,
        "blocks": blocks,
        "references": refs
    }

def process_document_citations(doc):
    """
    Analyzes document text blocks and references to map where each reference is cited.
    """
    if not doc or "error" in doc:
        return []

    citations = []
    ref_keys = {}
    
    # Map target_id to search keys
    for ref in doc["references"]:
        target_id = ref["target_id"]
        ref_text = ref["text"] or ""
        
        # Look for numbers/year (e.g. 24/2005)
        numbers = LAW_NUM_PATTERN.findall(ref_text)
        keys = set(numbers)
        
        # Look for common named entities
        if "Constitución" in ref_text or "C.E." in ref_text:
            keys.add("Constitución")
        if "Código Civil" in ref_text:
            keys.add("Código Civil")
        if "Código Penal" in ref_text:
            keys.add("Código Penal")
            
        if keys:
            if target_id not in ref_keys:
                ref_keys[target_id] = []
            ref_keys[target_id].append((keys, ref))

    # Scan each text block (article) for citation keys
    for block in doc["blocks"]:
        block_name = block["name"]
        block_text = block["text"]
        
        for target_id, ref_list in ref_keys.items():
            for keys, ref in ref_list:
                matched = False
                for key in keys:
                    if key in block_text:
                        matched = True
                        break
                
                if matched:
                    # Resolve target article/provision being cited
                    target_article = None
                    
                    # 1. Check reference metadata description text (most specific)
                    ref_text = ref["text"] or ""
                    art_match = ARTICLE_PATTERN.search(ref_text)
                    if art_match:
                        target_article = art_match.group(1).strip()
                    else:
                        # 2. Fallback: Check local context in block text surrounding the matched key
                        for key in keys:
                            idx = block_text.find(key)
                            if idx != -1:
                                start = max(0, idx - 100)
                                end = min(len(block_text), idx + len(key) + 100)
                                context = block_text[start:end]
                                art_match = ARTICLE_PATTERN.search(context)
                                if art_match:
                                    target_article = art_match.group(1).strip()
                                    break
                                    
                    citations.append({
                        "from_law": doc["boe_id"],
                        "from_article": block_name,
                        "to_law": target_id,
                        "to_article": target_article or "Todo",
                        "relation_type": ref["relation"],
                        "context": ref_text
                    })
                    
    return citations

def worker_task(file_path):
    """
    Multiprocessing worker task: parses XML and extracts citations.
    """
    doc = parse_xml_file(file_path)
    return process_document_citations(doc)

def main():
    parser = argparse.ArgumentParser(
        description="BOE Citation Extractor - Extract law-to-law article citations from XML files."
    )
    parser.add_argument("--limit", "-l", type=int, default=None,
                        help="Limit the number of XML files to process (useful for testing).")
    parser.add_argument("--output", "-o", type=str, default="data/extracted_citations.csv",
                        help="Path to save the output CSV edge list (default: data/extracted_citations.csv).")
    parser.add_argument("--workers", "-w", type=int, default=None,
                        help="Number of parallel worker processes (default: all CPU cores).")
    args = parser.parse_args()

    # Find XML files recursively
    script_dir = os.path.dirname(os.path.abspath(__file__))
    data_dir = os.path.join(script_dir, "data")
    xml_files = glob.glob(os.path.join(data_dir, "**/xml/*.xml"), recursive=True)
    
    if not xml_files:
        print("No XML files found. Please make sure data/ is populated with XML files.")
        sys.exit(1)

    print(f"Found {len(xml_files)} XML files to process.")
    
    if args.limit:
        xml_files = xml_files[:args.limit]
        print(f"Limiting execution to the first {args.limit} files.")

    output_path = os.path.abspath(args.output)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    num_workers = args.workers or multiprocessing.cpu_count()
    print(f"Starting processing with {num_workers} parallel workers...")

    citations_count = 0
    errors_count = 0
    
    # Write to CSV incrementally to save memory and ensure safe progress
    with open(output_path, "w", encoding="utf-8", newline="") as csvfile:
        fieldnames = ["from_law", "from_article", "to_law", "to_article", "relation_type", "context"]
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()

        with ProcessPoolExecutor(max_workers=num_workers) as executor:
            future_to_file = {executor.submit(worker_task, f): f for f in xml_files}
            
            processed = 0
            total_files = len(xml_files)
            
            for future in as_completed(future_to_file):
                file_path = future_to_file[future]
                processed += 1
                try:
                    results = future.result()
                    if results:
                        writer.writerows(results)
                        citations_count += len(results)
                except Exception as e:
                    errors_count += 1
                    # print(f"\nError processing {file_path}: {e}")

                # Print periodic progress
                if processed % 100 == 0 or processed == total_files:
                    sys.stdout.write(f"\rProgress: {processed}/{total_files} files processed ({processed/total_files*100:.1f}%) | Citations found: {citations_count}")
                    sys.stdout.flush()

    print(f"\n\nProcessing complete!")
    print(f"  Total XML files processed: {processed}")
    print(f"  Total citations extracted: {citations_count}")
    print(f"  Failed files:              {errors_count}")
    print(f"  Output saved to:           {output_path}")

if __name__ == "__main__":
    main()
