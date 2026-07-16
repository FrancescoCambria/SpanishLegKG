#!/bin/bash
# Exit immediately if a command exits with a non-zero status
set -e

echo "=== Setting up virtual environment for Citation RAG Agent ==="

# Check Python version
if ! command -v python3 &> /dev/null; then
    echo "Error: python3 is not installed or not in PATH."
    exit 1
fi

echo "Using Python: $(which python3)"

# Determine the virtual environment directory
VENV_DIR="/home/cambria/gram3/.venv"

if [ -d "$VENV_DIR" ]; then
    echo "Found existing virtual environment at $VENV_DIR"
else
    VENV_DIR="venv"
    if [ ! -d "$VENV_DIR" ]; then
        echo "Creating virtual environment in 'venv' directory..."
        python3 -m venv venv
    else
        echo "Virtual environment 'venv' already exists."
    fi
fi

# Activate virtual environment
echo "Activating virtual environment..."
source "$VENV_DIR"/bin/activate

# Upgrade pip
echo "Upgrading pip..."
pip install --upgrade pip

# Install dependencies
echo "Installing dependencies from requirements.txt..."
pip install -r requirements.txt

echo "=== Setup complete! ==="
echo "To activate this environment in your terminal, run:"
echo "source venv/bin/activate"
