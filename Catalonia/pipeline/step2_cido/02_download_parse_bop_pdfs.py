#!/usr/bin/env python3
"""
Step 2.4: If in BoPB, BoPG, BoPL, BoPT download the PDF and parse it to get structured text.
Reflects Step 2 in graph_construction_pipeline.txt
"""

import os
import sys
import subprocess

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PIPELINE_DIR = os.path.dirname(SCRIPT_DIR)
CAT_ROOT = os.path.dirname(PIPELINE_DIR)

def run_step2_bop_download_parse():
    print("=======================================================")
    print("[Step 2.4] Downloading & Parsing Provincial Bulletins (BOPB, BOPG, BOPL, BOPT)")
    print("=======================================================")
    
    # 1. Download BOPG / BOPT documents
    bopg_script = os.path.join(CAT_ROOT, "scripts", "download_bopg_bopt_documents.py")
    if os.path.exists(bopg_script):
        print("\n--> Downloading BOPG / BOPT documents...")
        subprocess.run([sys.executable, bopg_script], cwd=CAT_ROOT)

    # 2. Download BOPL documents
    bopl_script = os.path.join(CAT_ROOT, "scripts", "download_bopl_documents.py")
    if os.path.exists(bopl_script):
        print("\n--> Downloading BOPL documents...")
        subprocess.run([sys.executable, bopl_script], cwd=CAT_ROOT)

    # 3. Parse PDF documents to structured JSON text
    pdf_parse_script = os.path.join(CAT_ROOT, "scripts", "process_pdf_documents.py")
    if os.path.exists(pdf_parse_script):
        print("\n--> Parsing PDF documents to structured JSON text...")
        subprocess.run([sys.executable, pdf_parse_script], cwd=CAT_ROOT)

    return True

if __name__ == "__main__":
    success = run_step2_bop_download_parse()
    sys.exit(0 if success else 1)
