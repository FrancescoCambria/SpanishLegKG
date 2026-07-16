import os
import sys
import json
import argparse
from collections import Counter

def main():
    parser = argparse.ArgumentParser(description="Analyze scraped DOGC documents dataset")
    parser.add_argument("--file", type=str, default=None, help="Path to scraped JSON file (defaults to data/dogc_documents.json)")
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
        print(f"Error: Output file does not exist at {file_path}")
        print("Please run get_dogc_documents.py first to generate the dataset.")
        sys.exit(1)

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            documents = json.load(f)
    except Exception as e:
        print(f"Error: Failed to load '{display_name}': {e}")
        sys.exit(1)

    total_docs = len(documents)
    print("=" * 60)
    print(f"DOGC DATASET ANALYSIS: {display_name}")
    print("=" * 60)
    print(f"Total documents found: {total_docs}")

    # Group by dogcNumber
    by_dogc = {}
    for doc in documents:
        dogc_num = doc.get("dogcNumber")
        if dogc_num is None or dogc_num == "":
            dogc_num = "(empty/missing)"
        by_dogc.setdefault(str(dogc_num), []).append(doc)

    print(f"Unique DOGC issues: {len(by_dogc)}")
    print("\nDocuments per DOGC issue:")
    print("-" * 30)
    
    # Sort DOGCs
    def sort_key(k):
        if k == "(empty/missing)":
            return (0, "")
        try:
            # Sort numerically
            num_part = "".join(c for c in k if c.isdigit())
            return (1, int(num_part) if num_part else 0, k)
        except ValueError:
            return (2, 0, k)

    sorted_dogcs = sorted(by_dogc.keys(), key=sort_key)
    for dogc_num in sorted_dogcs:
        print(f"  DOGC {dogc_num}: {len(by_dogc[dogc_num])} documents")

    # Audit for missing fields
    missing_counts = Counter()
    missing_docs_details = []

    critical_fields = ["documentId", "htmlUrl", "pdfUrl", "title", "organisme", "section", "dateDOGC", "year"]

    for idx, doc in enumerate(documents):
        missing_fields_in_doc = []
        for field in critical_fields:
            val = doc.get(field)
            if val is None or val == "" or val == "None":
                missing_counts[field] += 1
                missing_fields_in_doc.append(field)
        
        # Keep track of detailed missing info for critical fields (documentId and htmlUrl)
        if "documentId" in missing_fields_in_doc or "htmlUrl" in missing_fields_in_doc:
            missing_docs_details.append({
                "index": idx,
                "dogcNumber": doc.get("dogcNumber", "(missing)"),
                "title": doc.get("title", "(missing)"),
                "missing": missing_fields_in_doc
            })

    print("\n" + "=" * 60)
    print("AUDIT FOR MISSING FIELDS")
    print("=" * 60)
    
    any_missing = False
    for field in critical_fields:
        count = missing_counts[field]
        pct = (count / total_docs) * 100 if total_docs > 0 else 0
        if count > 0:
            any_missing = True
            print(f"  Missing '{field}': {count} documents ({pct:.2f}%)")
        else:
            print(f"  Missing '{field}': 0 documents (0.00%)")

    if not any_missing:
        print("\n🎉 Perfect dataset! No documents are missing any metadata fields.")
    else:
        print(f"\n⚠️ Audited {total_docs} documents: {len(missing_docs_details)} documents have critical issues (missing 'documentId' or 'htmlUrl').")
        
        if missing_docs_details:
            print("\nDetailed list of documents missing 'documentId' or 'htmlUrl':")
            print("-" * 80)
            for item in missing_docs_details[:30]:  # Cap details output at 30 to keep it readable
                print(f"  Doc Index: {item['index']}")
                print(f"  DOGC Issue: {item['dogcNumber']}")
                print(f"  Title: {item['title']}")
                print(f"  Missing Fields: {', '.join(item['missing'])}")
                print("-" * 80)
            if len(missing_docs_details) > 30:
                print(f"  ... and {len(missing_docs_details) - 30} more documents with missing fields.")

if __name__ == "__main__":
    main()
