#!/usr/bin/env python3
"""
Step 1.1 & 1.2: Check for new DOGC issues & scrape document index.
Reflects Step 1 in graph_construction_pipeline.txt
"""

import os
import sys
import subprocess

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
STEP1_DIR = SCRIPT_DIR
PIPELINE_DIR = os.path.dirname(STEP1_DIR)
CAT_ROOT = os.path.dirname(PIPELINE_DIR)

def run_step1_scrape():
    print("=======================================================")
    print("[Step 1.1 & 1.2] Refreshing DOGC Document Index")
    print("=======================================================")
    
    cmd = [sys.executable, os.path.join(CAT_ROOT, "get_dogc_documents.py")]
    res = subprocess.run(cmd, cwd=CAT_ROOT)
    return res.returncode == 0

if __name__ == "__main__":
    success = run_step1_scrape()
    sys.exit(0 if success else 1)
