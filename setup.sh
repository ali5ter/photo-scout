#!/usr/bin/env bash
# @file setup.sh
# @brief Set up the photo-scout environment: Ollama, vision model, and Python dependencies.
# @description
#   Idempotent setup script for macOS. Installs pfb and Ollama via Homebrew if
#   absent, starts the Ollama service, pulls the chosen vision model, creates a
#   Python virtual environment, and installs pip dependencies.
#   Safe to run multiple times.
#
# @author Alister Lewis-Bowen <alister@lewis-bowen.org>
# @version 0.3.0
# @date 2026-05-07
# @license MIT
#
# @usage ./setup.sh [MODEL]
#   MODEL  Ollama vision model to pull (default: llava:7b)
#          Recommended alternatives: moondream (fast/small), llava-phi3 (middle ground)
#
# @example
#   ./setup.sh
#   ./setup.sh moondream
#   ./setup.sh llava-phi3
#
# @exitcodes
#   0  Success
#   1  Not running on macOS, or Homebrew not installed, or Ollama failed to start
#   2  Model pull failed
#   3  Python 3.10+ not found
#   4  pip install failed

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODEL="${1:-llava:7b}"

# ---------------------------------------------------------------------------
# Platform guard
# ---------------------------------------------------------------------------

if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "Error: photo-scout requires macOS (Photos.app is macOS-only)." >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Homebrew — required for all subsequent installs
# ---------------------------------------------------------------------------

if ! command -v brew &>/dev/null; then
    echo "Error: Homebrew is required but not installed." >&2
    echo "Install it from https://brew.sh then re-run this script." >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# pfb — install via brew if absent, then source
# ---------------------------------------------------------------------------

if ! command -v pfb &>/dev/null; then
    echo "Installing pfb..."
    brew install ali5ter/tap/pfb
fi

# pfb ends with [[ BASH_SOURCE == $0 ]] which returns 1 when sourced (expected).
# The || true prevents set -e from treating that as a failure.
# shellcheck disable=SC1090
source "$(brew --prefix)/bin/pfb" || true

# ---------------------------------------------------------------------------
# jq and exiftool — used by embed-metadata.sh
# ---------------------------------------------------------------------------

for _tool in jq exiftool; do
    if ! command -v "${_tool}" &>/dev/null; then
        pfb info "Installing ${_tool} via Homebrew..."
        brew install "${_tool}"
    fi
done
pfb success "jq and exiftool are available"

# ---------------------------------------------------------------------------
# Ollama — install and start
# ---------------------------------------------------------------------------

pfb heading "Ollama" "🦙"

if ! command -v ollama &>/dev/null; then
    pfb info "Ollama is not installed — installing via Homebrew..."
    brew install ollama
fi

pfb success "Ollama is installed"

_wait_for_ollama() {
    # @description Poll until the Ollama API is responsive (up to 15 seconds).
    # @return 0 if ready, 1 if timed out.
    local attempts=0
    while ! ollama list &>/dev/null 2>&1; do
        ((attempts++))
        [[ ${attempts} -ge 15 ]] && return 1
        sleep 1
    done
}

if ! ollama list &>/dev/null 2>&1; then
    pfb info "Starting Ollama service..."
    brew services start ollama &>/dev/null || true
    if ! _wait_for_ollama; then
        pfb error "Ollama did not start within 15 seconds."
        pfb info "Try starting it manually: brew services start ollama"
        exit 1
    fi
fi

pfb success "Ollama is running"

# ---------------------------------------------------------------------------
# Vision model
# ---------------------------------------------------------------------------

pfb heading "Vision model: ${MODEL}" "👁"

if ollama list | grep -q "^${MODEL}"; then
    pfb success "Model '${MODEL}' is already available"
else
    pfb info "Pulling '${MODEL}' — this may take several minutes..."
    pfb subheading "Sizes: moondream ~1.7 GB | llava-phi3 ~2.9 GB | llava:7b ~4.1 GB"
    if ! ollama pull "${MODEL}"; then
        pfb error "Failed to pull model '${MODEL}'."
        pfb info "Check available models at https://ollama.com/library and try again."
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
    pfb info "Install it: brew install python"
    exit 3
fi

PYTHON_VERSION="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"

if [[ $(python3 -c "import sys; print(int(sys.version_info >= (3, 10)))") -ne 1 ]]; then
    pfb error "Python 3.10+ required (found ${PYTHON_VERSION})."
    pfb info "Upgrade: brew install python"
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
    pfb info "Try manually: ${VENV_DIR}/bin/pip install -r requirements.txt"
    exit 4
fi

pfb success "Dependencies installed"

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------

pfb heading "Setup complete" "✅"
pfb subheading "Activate the environment: source .venv/bin/activate"
pfb subheading "Then run:                  python photo_scout.py --help"
pfb subheading "Quick test (10 photos):    python photo_scout.py --model ${MODEL} --limit 10"
