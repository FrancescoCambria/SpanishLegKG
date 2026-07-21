#!/bin/bash
# Exit immediately if a command exits with a non-zero status
set -e

# Directory of this script
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
cd "$SCRIPT_DIR"

# Path to the virtual environment
if [ -d "/home/cambria/gram3/.venv" ]; then
    VENV_DIR="/home/cambria/gram3/.venv"
else
    VENV_DIR="venv"
fi

# Ensure venv is set up
if [ ! -d "$VENV_DIR" ]; then
    echo "Virtual environment not found. Running setup_env.sh..."
    bash setup_env.sh
fi

echo "Activating virtual environment..."
source "$VENV_DIR"/bin/activate

# Default arguments
INPUT_JSON="../extracted_subgraph_large.json"
OUTPUT_JSON="../extracted_subgraph_large_updated.json"
VLLM_URL="http://127.0.0.1:8000/v1"
VLLM_MODEL="/gpfs/projects/bsc100/models/DeepSeek-R1-Distill-Qwen-32B"
MAX_CANDIDATES=15
MAX_NODES="" # Empty means all nodes

# Allow overriding default inputs via command line arguments
while [[ "$#" -gt 0 ]]; do
    case $1 in
        --input-json) INPUT_JSON="$2"; shift ;;
        --output-json) OUTPUT_JSON="$2"; shift ;;
        --vllm-url) VLLM_URL="$2"; shift ;;
        --vllm-model) VLLM_MODEL="$2"; shift ;;
        --max-candidates) MAX_CANDIDATES="$2"; shift ;;
        --max-nodes) MAX_NODES="--max-nodes $2"; shift ;;
        *) echo "Unknown parameter passed: $1"; exit 1 ;;
    esac
    shift
done

# Run the multi-step simple agent v3 loop
echo "Starting Simple Citation Agent V3 (Multi-step Detection -> Decomposition & Standardization -> Matching & Reconciliation)..."
python simple_agent_v3.py \
  --input-json "$INPUT_JSON" \
  --output-json "$OUTPUT_JSON" \
  --vllm-url "$VLLM_URL" \
  --vllm-model-name "$VLLM_MODEL" \
  --max-candidates "$MAX_CANDIDATES" \
  $MAX_NODES \
  --batch-size 50
