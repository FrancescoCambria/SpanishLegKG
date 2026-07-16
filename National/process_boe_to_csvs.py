#!/usr/bin/env python3
"""
BOE XML Parser and CSV Exporter
Parses downloaded BOE XML files to extract laws, articles, and citations.
Outputs 3 clean relational CSV files:
  1. data/laws.csv (id, title, publication_date, department, rango)
  2. data/articles.csv (id, law_id, name, text)
  3. data/citations.csv (citing_law, citing_article, cited_law, cited_article, relation_type, context)
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

# Regex patterns for citation extraction
ARTICLE_PATTERN = re.compile(
    r"\b(?:art(?:ículos?|ículo|\.)?|apartados?|disposici(?:ón|ones)\s+adicional(?:es)?)\s+([0-9a-zA-ZáéíóúÁÉÍÓÚñÑ\.\(\)\s,y\-]+?)(?=\s+(?:de|del|en|para|por)\b|$)",
    re.IGNORECASE
)
LAW_NUM_PATTERN = re.compile(r"\b\d+/\d{4}\b")

def parse_xml_file(file_path, sig_map=None):
    """
    Parses a single BOE XML file to extract law metadata, articles, and references.
    Supports a hybrid strategy: resolving XML-tagged references and extracting untagged text citations.
    """
    try:
        tree = ET.parse(file_path)
        root = tree.getroot()
    except Exception as e:
        return None

    meta = root.find("metadatos")
    if meta is None:
        return None
        
    boe_id = meta.findtext("identificador")
    title = meta.findtext("titulo")
    pub_date = meta.findtext("fecha_publicacion")
    dept = meta.findtext("departamento")
    rango = meta.findtext("rango")
    
    # 1. Law Node Metadata
    law_data = {
        "id": boe_id,
        "title": title,
        "publication_date": pub_date,
        "department": dept,
        "rango": rango
    }
    
    # 2. Extract Structural Blocks (Articles)
    blocks = []
    current_block = "Preámbulo"
    current_text = []
    
    texto_el = root.find("texto")
    if texto_el is not None:
        for p in texto_el.findall("p"):
            p_class = p.get("class")
            p_text = (p.text or "").strip()
            if not p_text:
                continue
                
            if p_class == "articulo":
                if current_text:
                    blocks.append({
                        "name": current_block.replace("[precepto]", "").strip(),
                        "text": "\n".join(current_text)
                    })
                current_block = p_text
                current_text = []
            else:
                current_text.append(p_text)
                
        if current_text:
            blocks.append({
                "name": current_block.replace("[precepto]", "").strip(),
                "text": "\n".join(current_text)
            })

    article_data = []
    for block in blocks:
        art_id = f"{boe_id}#{block['name']}"
        article_data.append({
            "id": art_id,
            "law_id": boe_id,
            "name": block["name"],
            "text": block["text"]
        })

    # 3. Extract Reference Editorial Links
    refs = []
    ref_el = root.find("analisis/referencias")
    if ref_el is not None:
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

    # 4. Map Citations to Citing Articles
    citations = []
    ref_keys = {}
    for ref in refs:
        target_id = ref["target_id"]
        ref_text = ref["text"] or ""
        
        numbers = LAW_NUM_PATTERN.findall(ref_text)
        keys = set(numbers)
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

    # Keep track of resolved citations to avoid duplicates within same block
    resolved_pairs = set()

    for block in blocks:
        block_name = block["name"]
        block_text = block["text"]
        
        # Strategy A: Extract tagged references
        for target_id, ref_list in ref_keys.items():
            for keys, ref in ref_list:
                matched = False
                for key in keys:
                    if key in block_text:
                        matched = True
                        break
                
                if matched:
                    target_article = None
                    ref_text = ref["text"] or ""
                    art_match = ARTICLE_PATTERN.search(ref_text)
                    if art_match:
                        target_article = art_match.group(1).strip()
                    else:
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
                        "citing_law": boe_id,
                        "citing_article": block_name,
                        "cited_law": target_id,
                        "cited_article": target_article or "Todo",
                        "relation_type": ref["relation"],
                        "context": ref_text
                    })
                    resolved_pairs.add((block_name, target_id))

        # Strategy B: Find untagged text citations matching signatures in database
        if sig_map:
            sig_pattern = re.compile(
                r"\b(Ley\s+Orgánica|Ley|Real\s+Decreto-ley|Real\s+Decreto|Decreto-ley|Decreto|Orden\s+[A-Z]+(?:/[A-Z0-9]+)?|Orden|Resolución)\s+(?:n.º\s+)?(\d+/\d{4})\b",
                re.IGNORECASE
            )
            preceding_art_pattern = re.compile(
                r"\b(?:art(?:ículos?|ículo|\.)?|apartados?|disposici(?:ón|ones)\s+adicional(?:es)?)\s+([0-9a-zA-ZáéíóúÁÉÍÓÚñÑ\.\(\)\s,y\-]+?)\s+(?:de\s+la|de\s+lo|del|de|en|para)\s+$",
                re.IGNORECASE
            )
            
            for match in sig_pattern.finditer(block_text):
                sig_text = match.group(0)
                sig_norm = re.sub(r"\s+", " ", sig_text).lower().strip()
                
                if sig_norm in sig_map:
                    target_id = sig_map[sig_norm]
                    
                    if (block_name, target_id) in resolved_pairs:
                        continue
                        
                    # Extract target article from preceding text context
                    target_article = None
                    start_idx = match.start()
                    preceding_text = block_text[max(0, start_idx - 60):start_idx]
                    art_match = preceding_art_pattern.search(preceding_text)
                    if art_match:
                        target_article = art_match.group(1).strip()
                        
                    citations.append({
                        "citing_law": boe_id,
                        "citing_article": block_name,
                        "cited_law": target_id,
                        "cited_article": target_article or "Todo",
                        "relation_type": "CITA",
                        "context": sig_text
                    })
                    resolved_pairs.add((block_name, target_id))

    return {
        "law": law_data,
        "articles": article_data,
        "citations": citations
    }

def main():
    parser = argparse.ArgumentParser(
        description="BOE XML to Relational CSV Exporter."
    )
    parser.add_argument("--limit", "-l", type=int, default=None,
                        help="Limit the number of XML files to process (useful for testing).")
    parser.add_argument("--workers", "-w", type=int, default=None,
                        help="Number of XML parser workers (default: all CPU cores).")
    args = parser.parse_args()

    # Find XML files recursively in the data/ directory
    script_dir = os.path.dirname(os.path.abspath(__file__))
    data_dir = os.path.join(script_dir, "data")
    xml_files = glob.glob(os.path.join(data_dir, "**/xml/*.xml"), recursive=True)
    
    if not xml_files:
        print("No XML files found in data/ subdirectory.")
        sys.exit(1)
        
    if args.limit:
        xml_files = xml_files[:args.limit]
        print(f"Limiting execution to first {args.limit} files.")

    print(f"Found {len(xml_files)} files to process.")

    # Paths for database and outputs
    db_path = os.path.join(data_dir, "boe_documents.json")
    laws_csv = os.path.join(data_dir, "laws.csv")
    articles_csv = os.path.join(data_dir, "articles.csv")
    citations_csv = os.path.join(data_dir, "citations.csv")

    # Build law signature lookup map from database
    sig_pattern = re.compile(
        r"\b(Ley\s+Orgánica|Ley|Real\s+Decreto-ley|Real\s+Decreto|Decreto-ley|Decreto|Orden\s+[A-Z]+(?:/[A-Z0-9]+)?|Orden|Resolución)\s+(?:n.º\s+)?(\d+/\d{4})\b",
        re.IGNORECASE
    )
    sig_map = {}
    if os.path.exists(db_path):
        print("Building signature lookup map from boe_documents.json...")
        try:
            with open(db_path, "r", encoding="utf-8") as f:
                docs = json.load(f)
            for doc in docs:
                title = doc.get("titulo") or ""
                m = sig_pattern.search(title)
                if m:
                    sig = f"{m.group(1)} {m.group(2)}"
                    sig_norm = re.sub(r"\s+", " ", sig).lower().strip()
                    sig_map[sig_norm] = doc.get("identificador")
            print(f"Loaded {len(sig_map)} law signatures.")
        except Exception as e:
            print(f"Warning: Failed to load signatures: {e}")
    else:
        print("boe_documents.json database not found. Running in XML-only reference mode.")

    num_workers = args.workers or multiprocessing.cpu_count()
    print(f"Parsing XML files using {num_workers} parallel workers...")

    parsed_count = 0
    total_files = len(xml_files)

    # Open CSV files and write headers
    with open(laws_csv, "w", encoding="utf-8", newline="") as f_laws, \
         open(articles_csv, "w", encoding="utf-8", newline="") as f_articles, \
         open(citations_csv, "w", encoding="utf-8", newline="") as f_citations:
         
        w_laws = csv.DictWriter(f_laws, fieldnames=["id", "title", "publication_date", "department", "rango"])
        w_articles = csv.DictWriter(f_articles, fieldnames=["id", "law_id", "name", "text"])
        w_citations = csv.DictWriter(f_citations, fieldnames=["citing_law", "citing_article", "cited_law", "cited_article", "relation_type", "context"])

        w_laws.writeheader()
        w_articles.writeheader()
        w_citations.writeheader()

        with ProcessPoolExecutor(max_workers=num_workers) as executor:
            future_to_file = {executor.submit(parse_xml_file, f, sig_map): f for f in xml_files}
            
            for future in as_completed(future_to_file):
                parsed_count += 1
                if parsed_count % 1000 == 0 or parsed_count == total_files:
                    sys.stdout.write(f"\rProgress: {parsed_count}/{total_files} files processed ({parsed_count/total_files*100:.1f}%)")
                    sys.stdout.flush()
                
                try:
                    res = future.result()
                    if res:
                        w_laws.writerow(res["law"])
                        w_articles.writerows(res["articles"])
                        w_citations.writerows(res["citations"])
                except Exception as e:
                    # Ignore corrupted files or parsing anomalies silently in CLI output to keep stdout clean
                    pass

    print(f"\n\nProcessing complete!")
    print(f"  Laws saved to:       {laws_csv}")
    print(f"  Articles saved to:   {articles_csv}")
    print(f"  Citations saved to:  {citations_csv}")

if __name__ == "__main__":
    main()
