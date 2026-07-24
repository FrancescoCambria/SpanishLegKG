#!/usr/bin/env python3
"""
Step 1.5 & Step 2.6: Build final Graph Nodes & Edges JSON files in graph_data/
Reflects Step 1 & Step 2 node and edge creation in graph_construction_pipeline.txt
"""

import os
import sys
import subprocess

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PIPELINE_DIR = os.path.dirname(SCRIPT_DIR)
CAT_ROOT = os.path.dirname(PIPELINE_DIR)

def run_build_graph():
    print("=======================================================")
    print("[Step 1.5 & 2.6] Building Graph Nodes & Edges in graph_data/")
    print("=======================================================")
    
    script_path = os.path.join(CAT_ROOT, "scripts", "build_graph_dataset.py")
    cmd = [sys.executable, script_path]
    res = subprocess.run(cmd, cwd=CAT_ROOT)
    return res.returncode == 0

if __name__ == "__main__":
    success = run_build_graph()
    sys.exit(0 if success else 1)
