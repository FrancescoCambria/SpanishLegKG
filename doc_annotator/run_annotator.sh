#!/usr/bin/env bash
PORT=${1:-8888}
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
echo "Starting LawGraph Document Annotator on http://localhost:${PORT}..."
python3 "${DIR}/server.py" "${PORT}"
