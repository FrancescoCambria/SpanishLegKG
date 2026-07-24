#!/usr/bin/env python3
"""
Step 2.5: Use Regex to find citations in the BOP text.
Reflects Step 2 in graph_construction_pipeline.txt
"""

import os
import sys
import subprocess

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PIPELINE_DIR = os.path.dirname(SCRIPT_DIR)
CAT_ROOT = os.path.dirname(PIPELINE_DIR)

def run_step2_bop_citations():
    print("=======================================================")
    print("[Step 2.5] Extracting Section & Article Citations from BOP Text via Regex")
    print("=======================================================")
    
    script_path = os.path.join(CAT_ROOT, "scripts", "process_sections_pipeline.py")
    cmd = [sys.executable, script_path]
    res = subprocess.run(cmd, cwd=CAT_ROOT)
    return res.returncode == 0

if __name__ == "__main__":
    success = run_step2_bop_citations()
    sys.exit(0 if success else 1)
