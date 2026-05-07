#!/usr/bin/env bash
# @file setup.sh
# @brief Set up the photo-scout environment: Ollama, vision model, and Python dependencies.
# @description
#   Idempotent setup script that installs Ollama if absent, starts the service,
#   pulls the chosen vision model, creates a Python virtual environment, and
#   installs pip dependencies. Safe to run multiple times.
#
#   Supported platforms:
#     macOS  — installs Ollama via Homebrew (brew install ollama)
#     Linux  — installs Ollama via the official install script (Debian/Ubuntu/RPi)
#
# @author Alister Lewis-Bowen <alister@lewis-bowen.org>
# @version 0.2.0
# @date 2026-05-07
# @license MIT
#
# @usage ./setup.sh [MODEL]
#   MODEL  Ollama vision model to pull (default: llava:7b)
#          Recommended alternatives: moondream (fast/small), llava-phi3 (middle ground)
#          On Raspberry Pi use moondream — llava:7b requires 4+ GB free RAM
#
# @example
#   ./setup.sh
#   ./setup.sh moondream
#   ./setup.sh llava-phi3
#
# @exitcodes
#   0  Success
#   1  Unsupported platform, missing prerequisite, or Ollama failed to start
#   2  Model pull failed
#   3  Python 3.10+ not found
#   4  pip install failed

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODEL="${1:-llava:7b}"

# shellcheck source=lib/pfb/pfb.sh
source "${SCRIPT_DIR}/lib/pfb/pfb.sh"

# ---------------------------------------------------------------------------
# Platform detection
# ---------------------------------------------------------------------------

case "$(uname -s)" in
    Darwin) PLATFORM="macos" ;;
    Linux)  PLATFORM="linux" ;;
    *)
        pfb error "Unsupported platform: $(uname -s). Only macOS and Linux are supported."
        exit 1
        ;;
esac

# ---------------------------------------------------------------------------
# Ollama: install if absent
# ---------------------------------------------------------------------------

pfb heading "Ollama" "🦙"

_install_ollama_macos() {
    # @description Install Ollama on macOS via Homebrew.
    if ! command -v brew &>/dev/null; then
        pfb error "Homebrew is required to install Ollama on macOS."
        pfb info "Install Homebrew first: https://brew.sh"
        exit 1
    fi
    pfb info "Installing Ollama via Homebrew..."
    brew install ollama
}

_install_ollama_linux() {
    # @description Install Ollama on Linux via the official install script.
    # @side_effects Requires sudo; sets up a systemd service.
    if ! command -v curl &>/dev/null; then
        pfb error "curl is required to install Ollama on Linux."
        pfb info "Install it first: sudo apt-get install -y curl"
        exit 1
    fi
    pfb info "Installing Ollama via official install script..."
    curl -fsSL https://ollama.com/install.sh | sh
}

if ! command -v ollama &>/dev/null; then
    pfb info "Ollama is not installed — installing now..."
    case "${PLATFORM}" in
        macos) _install_ollama_macos ;;
        linux) _install_ollama_linux ;;
    esac
else
    pfb success "Ollama is installed"
fi

# ---------------------------------------------------------------------------
# Ollama: ensure the server is running
# ---------------------------------------------------------------------------

_start_ollama() {
    # @description Attempt to start the Ollama background service.
    # @return 0 if started successfully, 1 if it could not be started.
    case "${PLATFORM}" in
        macos)
            if command -v brew &>/dev/null && brew list ollama &>/dev/null 2>&1; then
                brew services start ollama &>/dev/null || true
            else
                pfb warn "Could not start Ollama automatically. Open the Ollama app from Applications."
                return 1
            fi
            ;;
        linux)
            if command -v systemctl &>/dev/null; then
                sudo systemctl start ollama 2>/dev/null || true
            else
                pfb warn "systemctl not found. Start Ollama manually: ollama serve &"
                return 1
            fi
            ;;
    esac
}

_wait_for_ollama() {
    # @description Poll until the Ollama API is responsive (up to 15 seconds).
    # @return 0 if ready, 1 if timed out.
    local attempts=0
    while ! ollama list &>/dev/null 2>&1; do
        ((attempts++))
        if [[ ${attempts} -ge 15 ]]; then
            return 1
        fi
        sleep 1
    done
}

if ! ollama list &>/dev/null 2>&1; then
    pfb info "Ollama server is not running — starting it..."
    _start_ollama || true
    if ! _wait_for_ollama; then
        pfb error "Ollama did not start within 15 seconds."
        pfb info "Start it manually and re-run this script:"
        case "${PLATFORM}" in
            macos) pfb subheading "Open the Ollama app, or: brew services start ollama" ;;
            linux) pfb subheading "sudo systemctl start ollama" ;;
        esac
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
    if [[ "${PLATFORM}" == "linux" ]]; then
        pfb subheading "On Raspberry Pi, moondream is recommended (llava:7b needs 4+ GB free RAM)"
    fi
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
    case "${PLATFORM}" in
        macos) pfb info "Install it: brew install python" ;;
        linux) pfb info "Install it: sudo apt-get install -y python3 python3-venv python3-pip" ;;
    esac
    exit 3
fi

PYTHON_VERSION="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"

if [[ $(python3 -c "import sys; print(int(sys.version_info >= (3, 10)))") -ne 1 ]]; then
    pfb error "Python 3.10+ required (found ${PYTHON_VERSION})."
    case "${PLATFORM}" in
        macos) pfb info "Upgrade: brew install python" ;;
        linux) pfb info "Upgrade: sudo apt-get install -y python3.11" ;;
    esac
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
