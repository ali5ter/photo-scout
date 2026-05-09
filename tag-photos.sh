#!/usr/bin/env bash
# @file tag-photos.sh
# @brief Tag photos in Photos.app with keywords based on photo-scout recommendations.
# @description
#   Reads a photo-scout JSON report and sets a Photos.app keyword on each
#   analysed photo matching its recommendation: <prefix>-submit, <prefix>-maybe,
#   or <prefix>-skip. Existing user keywords on the photos are preserved; any
#   prior photo-scout keywords (starting with the prefix) are replaced.
#
#   After tagging, open Photos.app and search for "photo-scout-submit" (or your
#   chosen prefix) to browse photos visually by recommendation. You can also
#   create a Smart Album (File → New Smart Album → Keyword contains "photo-scout")
#   to keep the view persistent.
#
#   Note: Photos.app must be open when the script runs. The "add to album"
#   AppleScript API is broken in Photos.app 11 (macOS 26+); keyword tagging
#   is used as a reliable alternative.
#
# @author Alister Lewis-Bowen <alister@lewis-bowen.org>
# @version 1.0.0
# @date 2026-05-09
# @license MIT
#
# @usage ./tag-photos.sh [OPTIONS]
#
# @example
#   ./tag-photos.sh
#   ./tag-photos.sh --report my-report.json
#   ./tag-photos.sh --prefix my-project
#   ./tag-photos.sh --clear
#
# @exitcodes
#   0  Success
#   1  Missing dependency or invalid argument
#   2  Report file not found or not valid JSON
#   3  osascript failed — Photos.app may not be running

set -euo pipefail

# shellcheck disable=SC1090
source "$(brew --prefix)/bin/pfb" || true

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

REPORT="photo-scout-report.json"
KEYWORD_PREFIX="photo-scout"
CLEAR=false

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

_usage() {
    # @description Print usage and exit.
    cat <<EOF
Usage: ./tag-photos.sh [OPTIONS]

Tags each analysed photo in Photos.app with a keyword matching its
recommendation (<prefix>-submit, <prefix>-maybe, or <prefix>-skip).
Existing user keywords are preserved; prior photo-scout keywords are replaced.

After running, search for "photo-scout-submit" in Photos.app to browse
recommended photos visually, or create a Smart Album (File → New Smart Album).

Options:
  --report FILE    Path to photo-scout JSON report (default: photo-scout-report.json)
  --prefix NAME    Keyword prefix (default: photo-scout)
                   Tags photos with: <prefix>-submit, <prefix>-maybe, <prefix>-skip
  --clear          Remove all photo-scout keywords without adding new ones
  --help           Show this help
EOF
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --report) REPORT="$2"; shift 2 ;;
        --prefix) KEYWORD_PREFIX="$2"; shift 2 ;;
        --clear)  CLEAR=true; shift ;;
        --help)   _usage ;;
        *) pfb error "Unknown option: $1"; pfb info "Run ./tag-photos.sh --help"; exit 1 ;;
    esac
done

# ---------------------------------------------------------------------------
# Validate inputs
# ---------------------------------------------------------------------------

if ! command -v jq &>/dev/null; then
    pfb error "jq is not installed."
    pfb info "Run ./setup.sh to install all dependencies."
    exit 1
fi

if [[ ! -f "${REPORT}" ]]; then
    pfb error "Report not found: ${REPORT}"
    pfb info "Run photo_scout.py first to generate a report."
    exit 2
fi

if ! jq empty "${REPORT}" 2>/dev/null; then
    pfb error "Report is not valid JSON: ${REPORT}"
    exit 2
fi

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# @description Apply or remove a photo-scout keyword on a batch of photos.
# @param $1  Keyword to apply (empty string to just strip existing prefix keywords)
# @param $2  Newline-separated list of Photos.app UUIDs
# @return 0 on success, 3 on osascript failure
_tag_batch() {
    local keyword="$1"
    local uuids="$2"
    local prefix="${KEYWORD_PREFIX}"

    # Build an AppleScript list literal: {"UUID1", "UUID2", ...}
    local uuid_list=""
    while IFS= read -r uuid; do
        [[ -z "${uuid}" ]] && continue
        uuid_list+="\"${uuid}\", "
    done <<< "${uuids}"
    uuid_list="${uuid_list%, }"

    [[ -z "${uuid_list}" ]] && return 0

    # The AppleScript preserves all non-photo-scout keywords and appends the
    # new one. An empty keyword string means clear-only (used by --clear).
    osascript <<APPLESCRIPT >/dev/null 2>&1 || { pfb error "osascript failed — is Photos.app running?"; exit 3; }
tell application "Photos"
    set uuidList to {${uuid_list}}
    set newKeyword to "${keyword}"
    set kwPrefix to "${prefix}-"
    repeat with theUUID in uuidList
        try
            set theItem to media item id (theUUID as text)
            set existingKW to keywords of theItem
            if existingKW is missing value then set existingKW to {}
            set filteredKW to {}
            repeat with kw in existingKW
                if not ((kw as text) starts with kwPrefix) then
                    copy (kw as text) to end of filteredKW
                end if
            end repeat
            if newKeyword is not "" then
                set filteredKW to filteredKW & {newKeyword}
            end if
            set keywords of theItem to filteredKW
        end try
    end repeat
end tell
APPLESCRIPT
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

pfb heading "Tagging photos in Photos.app" "🏷"
pfb subheading "Report: ${REPORT}"
pfb subheading "Prefix: ${KEYWORD_PREFIX}"

if [[ "${CLEAR}" == "true" ]]; then
    pfb info "Clearing all ${KEYWORD_PREFIX}-* keywords..."
    all_uuids=$(jq -r \
        '.[] | select(.error == "") | (.uuid // (.filename | split(".")[0]))' \
        "${REPORT}")
    count=$(echo "${all_uuids}" | grep -c '[^[:space:]]' || true)
    _tag_batch "" "${all_uuids}"
    pfb success "Cleared photo-scout keywords from ${count} photo(s)"
    pfb heading "Done" "✅"
    exit 0
fi

tagged=0
skipped=0

for recommendation in submit maybe skip; do
    keyword="${KEYWORD_PREFIX}-${recommendation}"

    uuids=$(jq -r \
        --arg rec "${recommendation}" \
        '.[] | select(.recommendation == $rec and .error == "") | (.uuid // (.filename | split(".")[0]))' \
        "${REPORT}")

    count=$(echo "${uuids}" | grep -c '[^[:space:]]' || true)

    if [[ "${count}" -eq 0 ]]; then
        pfb subheading "${keyword}: no photos"
        ((skipped++)) || true
        continue
    fi

    pfb info "Tagging ${count} photo(s) → ${keyword}"
    _tag_batch "${keyword}" "${uuids}"
    pfb success "Tagged ${count} photo(s) with '${keyword}'"
    tagged=$((tagged + count))
done

pfb heading "Done" "✅"
pfb subheading "Tagged: ${tagged} photo(s)"
pfb subheading "Search Photos.app for '${KEYWORD_PREFIX}-submit' to browse visually"
pfb subheading "Or: File → New Smart Album → Keyword contains '${KEYWORD_PREFIX}'"
