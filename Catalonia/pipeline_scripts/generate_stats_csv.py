import os
import sys
import json
import csv
from collections import Counter

def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    parent_dir = os.path.dirname(script_dir)
    data_dir = os.path.join(parent_dir, "data")
    os.makedirs(data_dir, exist_ok=True)

    input_path = os.path.join(data_dir, "dogc_documents.json")
    if not os.path.exists(input_path):
        # Fallback to Catalonia root
        input_path = os.path.join(parent_dir, "dogc_documents.json")
        if not os.path.exists(input_path):
            # Fallback to current working directory
            input_path = "dogc_documents.json"

    print(f"Loading documents from {input_path}...")
    try:
        with open(input_path, "r", encoding="utf-8") as f:
            docs = json.load(f)
    except Exception as e:
        print(f"Error loading {input_path}: {e}")
        return

    # 1. Documents per year
    year_counter = Counter(d.get("year", "Unknown") for d in docs)
    year_csv = os.path.join(data_dir, "docs_per_year.csv")
    with open(year_csv, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Year", "DocumentCount"])
        for year, count in sorted(year_counter.items()):
            writer.writerow([year, count])

    # 2. Documents per type
    type_counter = Counter(d.get("type", "Unknown") for d in docs)
    type_csv = os.path.join(data_dir, "docs_per_type.csv")
    with open(type_csv, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Type", "DocumentCount"])
        for doc_type, count in type_counter.most_common():
            writer.writerow([doc_type, count])

    # 3. Documents per dogc
    dogc_info = {}
    for d in docs:
        num = d.get("dogcNumber", "Unknown")
        date = d.get("dateDOGC", "")
        year = d.get("year", "")
        if num not in dogc_info:
            dogc_info[num] = {"date": date, "year": year, "count": 0}
        dogc_info[num]["count"] += 1

    dogc_csv = os.path.join(data_dir, "docs_per_dogc.csv")
    with open(dogc_csv, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["DogcNumber", "DateDOGC", "Year", "DocumentCount"])
        # Sort key handles numeric issue names properly
        sorted_keys = sorted(dogc_info.keys(), key=lambda x: int(x) if str(x).isdigit() else 999999)
        for num in sorted_keys:
            info = dogc_info[num]
            writer.writerow([num, info["date"], info["year"], info["count"]])

    print(f"Successfully generated stats CSVs in {data_dir}.")

if __name__ == "__main__":
    main()
