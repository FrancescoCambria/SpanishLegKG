import os
import re
import sys
import json
import argparse
from collections import Counter

def extract_type_from_title(title):
    if not title:
        return "Unknown"
    
    title_clean = title.strip()
    
    # List of known Catalan/Spanish document types
    KNOWN_TYPES = [
        "RESOLUCIÓ", "RESOLUCION", "RESOLUCIó", 
        "EDICTE", "EDICTO", 
        "ANUNCI", "ANUNCIO", 
        "ORDRE", "ORDEN", 
        "DECRET", "DECRETO", 
        "ACORD", "ACUERDO", 
        "LLEI", "LEY", 
        "DECRET LLEI", "DECRETO LEY", 
        "DECRET LEGISLATIU", "DECRETO LEGISLATIVO",
        "REIAL DECRET", "REAL DECRETO",
        "NOMENAMENT", "NOMBRAMIENTO",
        "CORRECCIÓ D'ERRADA", "CORRECCIÓ D'ERRADES",
        "CORRECCIÓN DE ERRATA", "CORRECCIÓN DE ERRATAS",
        "DICTAMEN", "CIRCULAR", "INSTRUCCIÓ", "INSTRUCCION",
        "ANUNCIS", "EDICTES", "ORDRES", "DECRETS", "LLEIS", "LEIS", "ORDE"
    ]
    
    # Sort by length descending to match multi-word phrases first
    KNOWN_TYPES.sort(key=len, reverse=True)
    
    # Clean leading whitespace and quotes
    match_str = title_clean.lstrip(" '\"-")
    
    for kt in KNOWN_TYPES:
        pattern = re.compile(r'^' + re.escape(kt) + r'\b', re.IGNORECASE)
        m = pattern.search(match_str)
        if m:
            if kt.upper() in ["RESOLUCIÓ", "RESOLUCIÓ DE", "RESOLUCIÓDE", "RESOLUCIÓ DE LA", "RESOLUCIó"]:
                return "RESOLUCIÓ"
            return kt.upper()
            
    # Fallback: Extract leading all-caps words
    tokens = match_str.split()
    upper_words = []
    caps_pattern = re.compile(r"^[A-ZÀ-ÜÇÏ]+(?:[''-][A-ZÀ-ÜÇÏ]+)*$")
    for token in tokens:
        clean_token = token.rstrip(",.:;()[]\"")
        if caps_pattern.match(clean_token):
            upper_words.append(clean_token)
        else:
            break
            
    if upper_words:
        return " ".join(upper_words).upper()
        
    return "Other"

def main():
    parser = argparse.ArgumentParser(description="Fill missing document types from title in DOGC dataset")
    parser.add_argument("--file", type=str, default=None, help="Path to JSON file (defaults to data/dogc_documents.json)")
    parser.add_argument("--force", action="store_true", help="Force recalculating type for all documents, overwriting existing values")
    args = parser.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    parent_dir = os.path.dirname(script_dir)
    
    if args.file is None:
        file_path = os.path.join(parent_dir, "data", "dogc_documents.json")
        display_name = "data/dogc_documents.json"
    else:
        file_path = os.path.abspath(args.file)
        display_name = args.file

    if not os.path.exists(file_path):
        print(f"Error: Dataset file does not exist at {file_path}")
        sys.exit(1)

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            documents = json.load(f)
    except Exception as e:
        print(f"Error: Failed to load '{display_name}': {e}")
        sys.exit(1)

    total_docs = len(documents)
    filled_count = 0
    already_had_count = 0

    print("=" * 60)
    print(f"DOCUMENT TYPE FILLER: {display_name}")
    print("=" * 60)

    for doc in documents:
        existing_type = doc.get("type")
        has_valid_type = existing_type and existing_type not in ["", "Unknown", "Other"]

        if args.force or not has_valid_type:
            title = doc.get("title", "")
            inferred_type = extract_type_from_title(title)
            doc["type"] = inferred_type
            filled_count += 1
        else:
            already_had_count += 1

    # Count frequencies of the final types
    type_counts = Counter(doc.get("type", "Unknown") for doc in documents)

    # Save changes back to the file
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(documents, f, indent=2, ensure_ascii=False)
        print(f"Successfully processed {total_docs} documents.")
        print(f"  - Already had valid type: {already_had_count}")
        print(f"  - Inferred/Updated type: {filled_count}")
        print(f"Updated file saved back to: {file_path}")
    except Exception as e:
        print(f"Error: Failed to save changes to '{display_name}': {e}")
        sys.exit(1)

    print("\n" + "=" * 60)
    print("DOCUMENT TYPE DISTRIBUTION")
    print("=" * 60)
    for doc_type, count in type_counts.most_common(20):
        print(f"  {doc_type}: {count} documents")
    if len(type_counts) > 20:
        print(f"  ... and {len(type_counts) - 20} other types.")

if __name__ == "__main__":
    main()
