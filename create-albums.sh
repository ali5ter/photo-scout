#!/usr/bin/env bash
# @file create-albums.sh
# @brief Create Photos.app albums from a photo-scout report.
# @description
#   Reads a photo-scout JSON report and uses osascript to create albums in
#   Photos.app named <prefix>-submit, <prefix>-maybe, and <prefix>-skip,
#   then adds each analysed photo to the appropriate album. Existing photos
#   in the albums are not duplicated. Photos.app must be open on the same
#   machine.
#
# @author Alister Lewis-Bowen <alister@lewis-bowen.org>
# @version 1.0.0
# @date 2026-05-09
# @license MIT
#
# @usage ./create-albums.sh [OPTIONS]
#
# @example
#   ./create-albums.sh
#   ./create-albums.sh --report my-report.json
#   ./create-albums.sh --prefix my-project
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
ALBUM_PREFIX="photo-scout"

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

_usage() {
    # @description Print usage and exit.
    cat <<EOF
Usage: ./create-albums.sh [OPTIONS]

Options:
  --report FILE    Path to photo-scout JSON report (default: photo-scout-report.json)
  --prefix NAME    Album name prefix (default: photo-scout)
                   Creates albums: <prefix>-submit, <prefix>-maybe, <prefix>-skip
  --help           Show this help
EOF
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --report) REPORT="$2"; shift 2 ;;
        --prefix) ALBUM_PREFIX="$2"; shift 2 ;;
        --help)   _usage ;;
        *) pfb error "Unknown option: $1"; pfb info "Run ./create-albums.sh --help"; exit 1 ;;
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

# @description Ensure a Photos.app album exists, creating it if needed.
# @param $1 Album name
# @return 0 on success, 3 on osascript failure
_ensure_album() {
    local album_name="$1"
    osascript <<APPLESCRIPT 2>/dev/null || { pfb error "osascript failed — is Photos.app running?"; exit 3; }
tell application "Photos"
    if not (exists album named "${album_name}") then
        make new album named "${album_name}"
    end if
end tell
APPLESCRIPT
}

# @description Add a list of photos (by UUID) to a Photos.app album.
# @param $1 Album name
# @param $2 Newline-separated list of UUIDs
# @side_effects Photos already in the album are skipped (no duplicates)
_add_uuids_to_album() {
    local album_name="$1"
    local uuids="$2"

    # Build an AppleScript list literal: {"UUID1", "UUID2", ...}
    local uuid_list=""
    while IFS= read -r uuid; do
        [[ -z "${uuid}" ]] && continue
        uuid_list+="\"${uuid}\", "
    done <<< "${uuids}"
    uuid_list="${uuid_list%, }"  # trim trailing comma and space

    [[ -z "${uuid_list}" ]] && return 0

    osascript <<APPLESCRIPT 2>/dev/null || { pfb error "osascript failed — is Photos.app running?"; exit 3; }
tell application "Photos"
    set uuidList to {${uuid_list}}
    set itemList to {}
    repeat with theUUID in uuidList
        try
            set theItem to media item id (theUUID as text)
            set end of itemList to theItem
        end try
    end repeat
    if (count of itemList) > 0 then
        add itemList to album named "${album_name}"
    end if
end tell
APPLESCRIPT
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

pfb heading "Creating Photos.app albums" "📸"
pfb subheading "Report: ${REPORT}"
pfb subheading "Prefix: ${ALBUM_PREFIX}"

for recommendation in submit maybe skip; do
    album_name="${ALBUM_PREFIX}-${recommendation}"

    uuids=$(jq -r \
        --arg rec "${recommendation}" \
        '.[] | select(.recommendation == $rec and .error == "") | (.uuid // (.filename | split(".")[0]))' \
        "${REPORT}")

    count=$(echo "${uuids}" | grep -c '[^[:space:]]' || true)

    if [[ "${count}" -eq 0 ]]; then
        pfb subheading "${album_name}: no photos"
        continue
    fi

    pfb step "${album_name}" "📁"
    _ensure_album "${album_name}"
    _add_uuids_to_album "${album_name}" "${uuids}"
    pfb success "Added ${count} photo(s) to '${album_name}'"
done

pfb heading "Done" "✅"
pfb subheading "Open Photos.app and look for albums starting with '${ALBUM_PREFIX}'"
