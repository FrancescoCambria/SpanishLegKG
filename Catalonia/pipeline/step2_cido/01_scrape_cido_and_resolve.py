#!/usr/bin/env python3
"""
Step 2.1, 2.2, 2.3: Check for new CIDO documents (with matèria), cross-map to DOGC, and resolve unresolved DOGC entries.
Reflects Step 2 in graph_construction_pipeline.txt
"""

import os
import sys
import subprocess

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PIPELINE_DIR = os.path.dirname(SCRIPT_DIR)
CAT_ROOT = os.path.dirname(PIPELINE_DIR)

def run_step2_cido_map(incremental=True):
    print("=======================================================")
    print("[Step 2.1 - 2.3] Refreshing CIDO Map & Resolving DOGC References")
    print("=======================================================")
    
    script_path = os.path.join(CAT_ROOT, "scripts", "generate_cido_map.py")
    cmd = [sys.executable, script_path]
    if incremental:
        cmd.append("--incremental")
        
    res = subprocess.run(cmd, cwd=CAT_ROOT)
    return res.returncode == 0

if __name__ == "__main__":
    incremental = "--reconstruct" not in sys.argv
    success = run_step2_cido_map(incremental=incremental)
    sys.exit(0 if success else 1)
