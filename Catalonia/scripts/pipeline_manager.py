#!/usr/bin/env python3
"""
pipeline_manager.py

Orchestrates the DOGC & CIDO Law Graph pipeline:
1. scrape_dogc: Fetch / update DOGC document metadata summary index.
2. generate_cido_map: Fetch / update CIDO records & map them to DOGC documents.
3. parse_recent_docs: Download & structure HTML/XML documents for recent years.
4. parse_sections: Parse section-level articles, preambles & citations for DOGC & BOP docs (>=2010).
5. build_graph: Build the unified knowledge graph JSON files (nodes & edges) per graph_schema.txt.

Usage:
  # Reconstruct everything from scratch:
  python3 scripts/pipeline_manager.py --mode reconstruct

  # Adjourn / update with newly published data:
  python3 scripts/pipeline_manager.py --mode update

  # Execute a single step:
  python3 scripts/pipeline_manager.py --step parse_sections
"""

import os
import sys
import subprocess
import argparse
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)

STEPS = {
    "scrape_dogc": {
        "description": "Fetch DOGC summary index up to latest issue",
        "command_reconstruct": [sys.executable, os.path.join(ROOT_DIR, "get_dogc_documents.py")],
        "command_update": [sys.executable, os.path.join(ROOT_DIR, "get_dogc_documents.py")]
    },
    "generate_cido_map": {
        "description": "Generate CIDO to DOGC document mapping",
        "command_reconstruct": [sys.executable, os.path.join(SCRIPT_DIR, "generate_cido_map.py")],
        "command_update": [sys.executable, os.path.join(SCRIPT_DIR, "generate_cido_map.py"), "--incremental"]
    },
    "parse_recent_docs": {
        "description": "Batch parse & structure recent documents (2024-2026)",
        "command_reconstruct": [sys.executable, os.path.join(SCRIPT_DIR, "batch_parse_recent.py")],
        "command_update": [sys.executable, os.path.join(SCRIPT_DIR, "batch_parse_recent.py")]
    },
    "parse_sections": {
        "description": "Parse section-level articles, preambles & citations for DOGC & BOP docs (>=2010)",
        "command_reconstruct": [sys.executable, os.path.join(SCRIPT_DIR, "process_sections_pipeline.py")],
        "command_update": [sys.executable, os.path.join(SCRIPT_DIR, "process_sections_pipeline.py")]
    },
    "build_graph": {
        "description": "Build graph nodes & edges JSON files per entity type in graph_data/",
        "command_reconstruct": [sys.executable, os.path.join(SCRIPT_DIR, "build_graph_dataset.py")],
        "command_update": [sys.executable, os.path.join(SCRIPT_DIR, "build_graph_dataset.py")]
    }
}

def run_step(step_name, mode="update"):
    if step_name not in STEPS:
        print(f"[Error] Unknown step '{step_name}'. Available steps: {list(STEPS.keys())}")
        return False

    step_info = STEPS[step_name]
    cmd = step_info["command_reconstruct"] if mode == "reconstruct" else step_info["command_update"]
    
    print(f"\n=======================================================")
    print(f"Executing Step: [{step_name}] - {step_info['description']}")
    print(f"Mode: {mode}")
    print(f"Command: {' '.join(cmd)}")
    print(f"=======================================================\n")
    
    start_time = time.time()
    try:
        res = subprocess.run(cmd, cwd=ROOT_DIR, check=True)
        elapsed = time.time() - start_time
        print(f"\n[Success] Step '{step_name}' completed in {elapsed:.2f}s.\n")
        return True
    except subprocess.CalledProcessError as e:
        print(f"\n[Error] Step '{step_name}' failed with exit code {e.returncode}.\n", file=sys.stderr)
        return False
    except Exception as e:
        print(f"\n[Error] Failed to execute step '{step_name}': {e}\n", file=sys.stderr)
        return False

def run_pipeline(mode="update", steps_to_run=None):
    print("=======================================================")
    print(f"  LAW GRAPH PIPELINE MANAGER")
    print(f"  Mode: {mode.upper()}")
    print("=======================================================")
    
    selected_steps = steps_to_run if steps_to_run else ["scrape_dogc", "generate_cido_map", "parse_recent_docs", "parse_sections", "build_graph"]
    
    start_total = time.time()
    for step in selected_steps:
        success = run_step(step, mode=mode)
        if not success:
            print(f"\n[Pipeline Aborted] Pipeline stopped due to failure in step '{step}'.")
            sys.exit(1)
            
    total_elapsed = time.time() - start_total
    print("=======================================================")
    print(f"  [Pipeline Completed Successfully in {total_elapsed:.2f}s]")
    print("=======================================================")

def main():
    parser = argparse.ArgumentParser(description="Pipeline manager to reconstruct from scratch or update/adjourn LawGraph dataset")
    parser.add_argument("--mode", choices=["update", "reconstruct"], default="update",
                        help="'update' (adjourn dataset with newer published stuff) or 'reconstruct' (rebuild from scratch)")
    parser.add_argument("--step", choices=list(STEPS.keys()), default=None,
                        help="Execute a single specific pipeline step")
    args = parser.parse_args()

    if args.step:
        success = run_step(args.step, mode=args.mode)
        sys.exit(0 if success else 1)
    else:
        run_pipeline(mode=args.mode)

if __name__ == "__main__":
    main()
