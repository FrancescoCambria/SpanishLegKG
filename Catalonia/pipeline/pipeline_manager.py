#!/usr/bin/env python3
"""
pipeline_manager.py

Orchestrates the Law Graph pipeline as defined in graph_data/graph_construction_pipeline.txt:

Step 1: Refresh DoGC
  1.1 & 1.2: Check for new DoGC & scrape document list
  1.3: Parse XML files to get text, descriptors & affectations
  1.4: Use Regex to get additional text/section citations
  1.5: Create new edges and nodes

Step 2: Refresh CIDO
  2.1 - 2.3: Check for new CIDO docs (with matèria), cross-map to DOGC & resolve unresolved
  2.4: Download & parse BoPB, BoPG, BoPL, BoPT PDFs to structured text
  2.5: Use Regex to find citations in BOP text
  2.6: Create nodes and edges in graph_data/

Usage:
  python3 pipeline/pipeline_manager.py --mode update
  python3 pipeline/pipeline_manager.py --step step1_dogc
  python3 pipeline/pipeline_manager.py --step step2_cido
  python3 pipeline/pipeline_manager.py --step build_graph
"""

import os
import sys
import subprocess
import argparse
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CAT_ROOT = os.path.dirname(SCRIPT_DIR)

STEP1_SCRIPTS = [
    ("1.1 & 1.2 Scrape DoGC", os.path.join(SCRIPT_DIR, "step1_dogc", "01_scrape_dogc.py")),
    ("1.3 Parse XML Text & Descriptors", os.path.join(SCRIPT_DIR, "step1_dogc", "02_parse_xml_structured.py")),
    ("1.4 Extract DoGC Citations", os.path.join(SCRIPT_DIR, "step1_dogc", "03_extract_dogc_citations.py"))
]

STEP2_SCRIPTS = [
    ("2.1 - 2.3 Scrape CIDO & Resolve DOGC", os.path.join(SCRIPT_DIR, "step2_cido", "01_scrape_cido_and_resolve.py")),
    ("2.4 Download & Parse BOP PDFs", os.path.join(SCRIPT_DIR, "step2_cido", "02_download_parse_bop_pdfs.py")),
    ("2.5 Extract BOP Citations", os.path.join(SCRIPT_DIR, "step2_cido", "03_extract_bop_citations.py"))
]

BUILD_GRAPH_SCRIPT = ("1.5 & 2.6 Build Nodes & Edges in graph_data/", os.path.join(SCRIPT_DIR, "graph_builder", "build_graph.py"))

def run_script(title, script_path, mode="update"):
    print(f"\n=======================================================")
    print(f"Executing: [{title}]")
    print(f"Script: {script_path}")
    print(f"Mode: {mode}")
    print(f"=======================================================\n")
    
    cmd = [sys.executable, script_path]
    if mode == "reconstruct" and "01_scrape_cido_and_resolve.py" in script_path:
        cmd.append("--reconstruct")

    start_time = time.time()
    res = subprocess.run(cmd, cwd=CAT_ROOT)
    elapsed = time.time() - start_time
    
    if res.returncode == 0:
        print(f"\n[Success] [{title}] completed in {elapsed:.2f}s.\n")
        return True
    else:
        print(f"\n[Error] [{title}] failed with exit code {res.returncode}.\n", file=sys.stderr)
        return False

def run_pipeline(step=None, mode="update"):
    start_total = time.time()
    
    if step == "step1_dogc" or step is None:
        print("\n>>> STARTING STEP 1: REFRESH DOGC <<<")
        for title, s_path in STEP1_SCRIPTS:
            if not run_script(title, s_path, mode):
                print("[Pipeline Aborted] Failure in Step 1.")
                sys.exit(1)
                
    if step == "step2_cido" or step is None:
        print("\n>>> STARTING STEP 2: REFRESH CIDO <<<")
        for title, s_path in STEP2_SCRIPTS:
            if not run_script(title, s_path, mode):
                print("[Pipeline Aborted] Failure in Step 2.")
                sys.exit(1)

    if step == "build_graph" or step is None:
        print("\n>>> BUILDING KNOWLEDGE GRAPH (NODES & EDGES) <<<")
        title, s_path = BUILD_GRAPH_SCRIPT
        if not run_script(title, s_path, mode):
            print("[Pipeline Aborted] Failure in Graph Construction.")
            sys.exit(1)
            
    total_elapsed = time.time() - start_total
    print("=======================================================")
    print(f"  [Pipeline Execution Successfully Completed in {total_elapsed:.2f}s]")
    print("=======================================================")

def main():
    parser = argparse.ArgumentParser(description="Law Graph Pipeline Manager adhering to graph_construction_pipeline.txt")
    parser.add_argument("--mode", choices=["update", "reconstruct"], default="update", help="'update' (incremental) or 'reconstruct' (from scratch)")
    parser.add_argument("--step", choices=["step1_dogc", "step2_cido", "build_graph"], default=None, help="Execute a specific stage only")
    args = parser.parse_args()

    run_pipeline(step=args.step, mode=args.mode)

if __name__ == "__main__":
    main()
