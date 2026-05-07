#!/usr/bin/env bash
# @file embed-metadata.sh
# @brief Embed IPTC/XMP metadata into copies of photos recommended by photo_scout.
# @description
#   Reads a photo-scout JSON report, filters photos by recommendation level,
#   copies each qualifying original to an output directory, and uses exiftool
#   to write keywords, subject, and caption into the copy's IPTC and XMP tags.
#   Originals in the Photos library are never modified.
#
# @author Alister Lewis-Bowen <alister@lewis-bowen.org>
# @version 0.1.0
# @date 2026-05-07
# @license MIT
#
# @usage ./embed-metadata.sh [OPTIONS]
#
# @example
#   ./embed-metadata.sh
#   ./embed-metadata.sh --report my-report.json
#   ./embed-metadata.sh --filter maybe --output ./ready
#   ./embed-metadata.sh --filter all --output ./all-tagged
#
# @exitcodes
#   0  Success
#   1  Missing dependency or invalid argument
#   2  Report file not found or not valid JSON

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck disable=SC1090
source "$(brew --prefix)/bin/pfb" || true

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

REPORT="photo-scout-report.json"
OUTPUT_DIR="ready-to-submit"
FILTER="submit"

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

_usage() {
    # @description Print usage and exit.
    cat <<EOF
Usage: ./embed-metadata.sh [OPTIONS]

Options:
  --report FILE    Path to photo-scout JSON report (default: photo-scout-report.json)
  --output DIR     Output directory for tagged copies (default: ready-to-submit/)
  --filter LEVEL   Which photos to process: submit | maybe | all (default: submit)
  --help           Show this help

Filter levels:
  submit  Only photos recommended for submission (highest confidence)
  maybe   Submit and maybe recommendations
  all     All photos that were analysed (including skip)
EOF
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --report) REPORT="$2"; shift 2 ;;
        --output) OUTPUT_DIR="$2"; shift 2 ;;
        --filter) FILTER="$2"; shift 2 ;;
        --help)   _usage ;;
        *) pfb error "Unknown option: $1"; pfb info "Run ./embed-metadata.sh --help"; exit 1 ;;
    esac
done

# ---------------------------------------------------------------------------
# Validate inputs
# ---------------------------------------------------------------------------

for _cmd in jq exiftool; do
    if ! command -v "${_cmd}" &>/dev/null; then
        pfb error "${_cmd} is not installed."
        pfb info "Run ./setup.sh to install all dependencies."
        exit 1
    fi
done

if [[ ! -f "${REPORT}" ]]; then
    pfb error "Report not found: ${REPORT}"
    pfb info "Run photo_scout.py first to generate a report."
    exit 2
fi

if ! jq empty "${REPORT}" 2>/dev/null; then
    pfb error "Report is not valid JSON: ${REPORT}"
    exit 2
fi

case "${FILTER}" in
    submit) JQ_FILTER='.[] | select(.recommendation == "submit")' ;;
    maybe)  JQ_FILTER='.[] | select(.recommendation == "submit" or .recommendation == "maybe")' ;;
    all)    JQ_FILTER='.[] | select(.error == "")' ;;
    *)
        pfb error "Invalid filter '${FILTER}'. Use: submit, maybe, or all."
        exit 1
        ;;
esac

# ---------------------------------------------------------------------------
# Process photos
# ---------------------------------------------------------------------------

pfb heading "Embedding metadata" "🏷"
pfb subheading "Report:  ${REPORT}"
pfb subheading "Output:  ${OUTPUT_DIR}"
pfb subheading "Filter:  ${FILTER}"

mkdir -p "${OUTPUT_DIR}"

total=$(jq "[${JQ_FILTER}] | length" "${REPORT}")

if [[ "${total}" -eq 0 ]]; then
    pfb warn "No photos matched filter '${FILTER}' in the report."
    exit 0
fi

pfb info "Processing ${total} photo(s)..."

count=0
skipped=0

while IFS= read -r photo_json; do
    original_path=$(jq -r '.original_path' <<< "${photo_json}")
    filename=$(jq -r '.filename' <<< "${photo_json}")
    subject=$(jq -r '.subject' <<< "${photo_json}")

    if [[ ! -f "${original_path}" ]]; then
        pfb warn "Original not found, skipping: ${filename}"
        ((skipped++)) || true
        continue
    fi

    output_file="${OUTPUT_DIR}/${filename}"
    cp "${original_path}" "${output_file}"

    # Build exiftool keyword arguments from the JSON array
    keyword_args=()
    while IFS= read -r kw; do
        keyword_args+=("-IPTC:Keywords=${kw}" "-XMP:Subject=${kw}")
    done < <(jq -r '.keywords[]' <<< "${photo_json}")

    exiftool \
        "${keyword_args[@]}" \
        "-IPTC:Caption-Abstract=${subject}" \
        "-IPTC:Headline=${subject}" \
        "-XMP:Description=${subject}" \
        -overwrite_original \
        -quiet \
        "${output_file}"

    ((count++)) || true
    pfb subheading "[${count}/${total}] ${filename}"

done < <(jq -c "${JQ_FILTER}" "${REPORT}")

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------

pfb heading "Done" "✅"
pfb subheading "Tagged:  ${count} photo(s) written to ${OUTPUT_DIR}/"
[[ "${skipped}" -gt 0 ]] && pfb warn "${skipped} photo(s) skipped (original file not found)"
pfb subheading "Review the copies before uploading — originals are unchanged."
