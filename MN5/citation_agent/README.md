# Citation RAG Agent for HPC Compute Nodes

This directory contains a complete, robust system to extract citations from legal text in an iterative and self-contained manner. It features a RAG (Retrieval-Augmented Generation) pipeline using FAISS and SentenceTransformers to retrieve few-shot examples from existing citations in the graph, boosting the accuracy of the extraction model.

## Folder Contents
- **`simple_agent.py`**: Base simple citation extraction agent.
- **`simple_agent_v2.py`**: Advanced citation verification and extraction agent. Includes existing detected citations in the LLM prompt to verify correctness and provides a Knowledge Graph Reference Catalog of existing document nodes.
- **`simple_agent_v3.py`**: Multi-stage citation pipeline breaking extraction into 3 distinct LLM prompts per node: (1) Raw citation detection, (2) Decomposition of compound citations & standardization into legal fields, and (3) Graph matching & reconciliation (KEEP/MODIFY/REMOVE/ADD).
- **`run_simple_agent_v2.sh`**: Wrapper script to run `simple_agent_v2.py`.
- **`run_simple_agent_v3.sh`**: Wrapper script to run `simple_agent_v3.py`.
- **`agent.py`**: The core Python script containing the dual-FAISS RAG pipeline (Few-Shot examples + Target Nodes resolver) and the vLLM extraction runner.
- **`setup_env.sh`**: Installs a local virtual environment (`venv`) and all requirements in user-space.
- **`requirements.txt`**: Standard dependencies (`numpy`, `faiss-cpu`, `sentence-transformers`, `openai`, `tqdm`).
- **`run_agent.sh`**: Wrapper script to run environment setup, build search indexes (if missing), and run the extraction loop.

---

## Architecture Overview

1. **Dual RAG Indexes**:
   - **Few-Shot Index**: Embeds and indexes texts of all sections that *already have* citations in the graph. When processing a new section, it fetches the top $K$ most similar sections and their citations to build a few-shot prompt for the LLM.
   - **Targets Index**: Embeds and indexes all node titles in the graph. When the LLM outputs a text string representing a cited document (e.g. `"Ley 13/2008"`), the agent performs string alignment and falls back to a semantic lookup in this index to automatically map it to the correct `target_id` in the graph.
   
2. **HPC Compatibility & Resilience**:
   - **Resumability**: The script saves checkpoints every 50 nodes (configurable via `--batch-size`) to `extracted_subgraph_large_updated.json`. If restarted, it reads the checkpoint file and skips nodes that have already been marked as `processed_by_llm_agent: true`.
   - **Signal Handling**: If a Slurm job timeout or keyboard interrupt (`SIGINT`, `SIGTERM`) occurs, the agent catches the signal, saves the current graph progress to disk, and exits cleanly without data corruption.
   - **Local Proxy Bypass**: Automatically configures `NO_PROXY` and forces OpenAI HTTP clients to bypass systemic network proxies, which prevents connection failures with local vLLM instances.

---

## Setup and Installation

The scripts will automatically detect and use your existing virtual environment at `/home/cambria/gram3/.venv` if present. If it is not found, they will create a local `venv` directory.

To ensure all required libraries (e.g. `faiss-cpu`, `sentence-transformers`, `openai`) are installed in the active environment, run:
```bash
chmod +x setup_env.sh run_agent.sh
./setup_env.sh
```

### Pre-downloading the Embedding Model (Optional but Recommended)
Compute nodes on HPC clusters often do not have internet access. You should download the multilingual SentenceTransformer model while on the login node (or any internet-connected node) so it is cached in your user directory:
```bash
# If using the global environment, activate it:
source /home/cambria/gram3/.venv/bin/activate
# Or if using the local env:
# source venv/bin/activate

python agent.py pre-download
```

---

## Running the Agent

Ensure your vLLM server is running (e.g. serving `DeepSeek-R1-Distill-Qwen-32B` on port 8000).

To start the agent:
```bash
./run_agent.sh
```

### Testing with a Limit
To test the script on a small set of nodes first, use the `--max-nodes` flag:
```bash
./run_agent.sh --max-nodes 10
```

### Script CLI Options (Advanced)
If you want to invoke `agent.py` directly, activate the virtual environment (`source venv/bin/activate`) and run:
```bash
python agent.py run \
  --input-json ../extracted_subgraph_large.json \
  --output-json ../extracted_subgraph_large_updated.json \
  --vllm-url http://127.0.0.1:8000/v1 \
  --vllm-model-name /gpfs/projects/bsc100/models/DeepSeek-R1-Distill-Qwen-32B \
  --few-shot-k 2 \
  --similarity-threshold 0.75
```
