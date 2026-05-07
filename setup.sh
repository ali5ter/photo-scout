#!/usr/bin/env bash
# @file setup.sh
# @brief Set up the photo-scout environment: Ollama model and Python dependencies.
# @description
#   Idempotent setup script that verifies Ollama is running, pulls the chosen
#   vision model if not already available, creates a Python virtual environment,
#   and installs pip dependencies. Safe to run multiple times.
#
# @author Alister Lewis-Bowen <alister@lewis-bowen.org>
# @version 0.1.0
# @date 2026-05-07
# @license MIT
#
# @usage ./setup.sh [MODEL]
#   MODEL  Ollama vision model to pull (default: llava:7b)
#
# @example
#   ./setup.sh
#   ./setup.sh moondream
#   ./setup.sh llava-phi3
#
# @exitcodes
#   0  Success
#   1  Ollama not running
#   2  Model pull failed
#   3  Python 3.10+ not found
#   4  pip install failed

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODEL="${1:-llava:7b}"

# shellcheck source=lib/pfb/pfb.sh
source "${SCRIPT_DIR}/lib/pfb/pfb.sh"

# ---------------------------------------------------------------------------
# Ollama
# ---------------------------------------------------------------------------

pfb heading "Checking Ollama" "🦙"

if ! command -v ollama &>/dev/null; then
    pfb error "Ollama is not installed."
    pfb info "Install it from https://ollama.com then re-run this script."
    exit 1
fi

if ! ollama list &>/dev/null 2>&1; then
    pfb error "Ollama is installed but not running."
    pfb info "Start it from the Ollama menu bar app, then re-run this script."
    exit 1
fi

pfb success "Ollama is running"

# ---------------------------------------------------------------------------
# Vision model
# ---------------------------------------------------------------------------

pfb heading "Vision model: ${MODEL}" "👁"

if ollama list | grep -q "^${MODEL}"; then
    pfb success "Model '${MODEL}' is already available"
else
    pfb info "Pulling '${MODEL}' — this may take several minutes depending on your connection..."
    pfb subheading "Model sizes: moondream ~1.7 GB | llava-phi3 ~2.9 GB | llava:7b ~4.1 GB"
    if ! ollama pull "${MODEL}"; then
        pfb error "Failed to pull model '${MODEL}'."
        pfb info "Check the model name at https://ollama.com/library and try again."
        exit 2
    fi
    pfb success "Model '${MODEL}' is ready"
fi

# ---------------------------------------------------------------------------
# Python environment
# ---------------------------------------------------------------------------

pfb heading "Python environment" "🐍"

if ! command -v python3 &>/dev/null; then
    pfb error "python3 is not installed."
    pfb info "Install it via Homebrew: brew install python"
    exit 3
fi

PYTHON_VERSION="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
REQUIRED_MAJOR=3
REQUIRED_MINOR=10

if [[ $(python3 -c "import sys; print(int(sys.version_info >= (${REQUIRED_MAJOR}, ${REQUIRED_MINOR})))") -ne 1 ]]; then
    pfb error "Python ${REQUIRED_MAJOR}.${REQUIRED_MINOR}+ required (found ${PYTHON_VERSION})."
    pfb info "Upgrade via Homebrew: brew install python"
    exit 3
fi

pfb success "Python ${PYTHON_VERSION} found"

VENV_DIR="${SCRIPT_DIR}/.venv"

if [[ ! -d "${VENV_DIR}" ]]; then
    pfb info "Creating virtual environment at .venv/"
    python3 -m venv "${VENV_DIR}"
fi

pfb info "Installing dependencies..."
if ! "${VENV_DIR}/bin/pip" install --quiet -r "${SCRIPT_DIR}/requirements.txt"; then
    pfb error "pip install failed."
    pfb info "Try: ${VENV_DIR}/bin/pip install -r requirements.txt"
    exit 4
fi

pfb success "Dependencies installed"

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------

pfb heading "Setup complete" "✅"
pfb subheading "Activate the environment: source .venv/bin/activate"
pfb subheading "Then run: python photo_scout.py --help"
pfb subheading "Quick test: python photo_scout.py --model ${MODEL} --limit 10"
