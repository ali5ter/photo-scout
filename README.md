# photo-scout

A command-line tool that analyses a macOS Photos library for stock photo suitability using a
locally-running Ollama vision model. Photos are scored on technical quality and commercial appeal,
producing a prioritised report of submission candidates.

Everything runs locally — no cloud services, no API costs, no data leaving the machine.

## Features

- Reads directly from the macOS Photos library (no export step needed)
- Analyses most recent photos first
- Skips already-analysed photos on subsequent runs (incremental)
- Filters by album, date range, or photo count
- Scores each photo on technical quality and commercial appeal (1–5 each)
- Recommends: **submit**, **maybe**, or **skip**
- Outputs JSON (default) or CSV; optional Markdown summary via `--markdown`
- Handles iCloud-only photos gracefully

## Requirements

- macOS (Photos.app and the Photos library are macOS-only)
- [Homebrew](https://brew.sh) — used to install all dependencies
- Python 3.10+
- Ollama and a vision model — installed automatically by `setup.sh`

## Installation

```bash
git clone https://github.com/ali5ter/photo-scout.git
cd photo-scout
./setup.sh
source .venv/bin/activate
```

`setup.sh` is idempotent — safe to re-run at any time. It installs via Homebrew:
[pfb](https://github.com/ali5ter/pfb), Ollama, jq, and exiftool; starts the Ollama
service; pulls the vision model; and sets up the Python virtual environment.

To use a different model at setup time:

```bash
./setup.sh moondream    # faster, smaller — good for testing
./setup.sh llava-phi3   # good middle ground
./setup.sh llava:7b     # default, best quality (needs ~4 GB free RAM)
```

## Model Choice

| Model | Size | Speed (Apple Silicon, 8 GB) | Quality |
| --- | --- | --- | --- |
| `moondream` | 1.7 GB | Fast (~5–10 s/image) | Pipeline testing only — too small for real analysis |
| `llava-phi3` | 2.9 GB | Moderate (~15–30 s/image) | Good |
| `llava:7b` | 4.1 GB | Slow (~30–60 s/image) | Best (recommended) |

On Apple Silicon with 8 GB unified memory, `llava:7b` (4 GB quantized) fits comfortably alongside
the OS. It is slow but produces genuinely useful analysis.

> **moondream warning:** moondream is too small to follow structured prompts reliably. It tends
> to echo the JSON template rather than analyse the image, producing identical scores and
> placeholder keywords for every photo. Use it only to verify the pipeline runs end-to-end,
> then switch to `llava:7b` for real analysis.

To add or switch models, use `setup.sh` rather than `ollama pull` directly — it ensures the
full environment is consistent:

```bash
./setup.sh llava:7b
```

## Usage

```bash
# Analyse the 20 most recent locally-available photos
python photo_scout.py --model moondream --limit 20

# Filter to a specific album
python photo_scout.py --album Landscapes

# Only photos taken since a date
python photo_scout.py --since 2024-01-01

# Force re-analysis of everything (ignore existing report)
python photo_scout.py --force

# Also write a human-readable Markdown report alongside the JSON
python photo_scout.py --markdown

# Output as CSV instead (for sorting in Numbers/Excel)
python photo_scout.py --format csv
```

Full option reference:

```text
--library PATH     Path to Photos library (default: system default)
--album NAME       Filter to photos in this album (exact title match)
--model MODEL      Ollama model (default: llava:7b)
--output FILE      Output file written to current directory (default: photo-scout-report.json)
--format FORMAT    json | csv (default: json)
--markdown         Also write a Markdown report alongside the primary output
--limit N          Max photos to process (selects most recent N)
--since YYYY-MM-DD Only photos taken on or after this date
--force            Re-analyse all photos, ignoring any existing report
```

## Output files

All output is written to the **current working directory** — wherever you run the script from.
Running from the project directory (`photo-scout/`) is recommended so the report is alongside
the other scripts.

| File | Created by | Description |
| --- | --- | --- |
| `photo-scout-report.json` | `photo_scout.py` | Analysis results (gitignored) |
| `ready-to-submit/` | `embed-metadata.sh` | Tagged copies of recommended photos (gitignored) |
| `ready-to-submit/submit/` | `embed-metadata.sh --organize` | Tagged submit photos in subfolder (gitignored) |
| `ready-to-submit/maybe/` | `embed-metadata.sh --organize` | Tagged maybe photos in subfolder (gitignored) |
| `ready-to-submit/skip/` | `embed-metadata.sh --organize` | Tagged skip photos in subfolder (gitignored) |

## Understanding the scores

Each photo receives two scores from the vision model, both on a 1–5 scale:

| Score | technical_score | commercial_score |
| --- | --- | --- |
| 1 | Very poor — blurry, severely over/under-exposed, heavy noise | No stock value — personal snapshots, identifiable faces without releases |
| 2 | Poor | Low appeal |
| 3 | Acceptable | Moderate appeal |
| 4 | Good | Good commercial potential |
| 5 | Excellent — sharp, well-exposed, strong composition | High value — concepts, lifestyle, nature, business, travel |

`overall_score` is the mean of the two (1.0–5.0).

`recommendation` is the model's holistic judgement:

- **submit** — strong candidate, worth uploading
- **maybe** — mixed signals, review manually before deciding
- **skip** — not suitable for stock

The scores inform the recommendation but don't mechanically determine it — a technically
excellent photo of something with no commercial market will still be `skip`.

## Incremental runs

The report file accumulates results across runs. On each run, photos already present in the
report are skipped automatically. This means you can run in batches:

```bash
# First pass — 50 most recent photos
python photo_scout.py --limit 50

# Next pass — picks up the next 50 not yet in the report
python photo_scout.py --limit 50

# Re-analyse everything from scratch
python photo_scout.py --force
```

## iCloud Photos

Photos stored only in iCloud (offloaded to save local disk space) are skipped automatically,
and the count is reported. To include them:

- **Download everything:** in Photos.app go to **Settings → iCloud** and select
  **Download Originals to this Mac** (requires sufficient disk space)
- **Download selectively:** select photos or an album in Photos.app, right-click, and choose
  **Download \[N\] Originals**

There is no way to trigger iCloud downloads programmatically from this script.

## Identifying photos from the report

Photos.app stores originals under internal UUID-based filenames (e.g.
`4A2F3D8E-1234-5678-ABCD-9012EFAB.HEIC`) that are not searchable in Photos.app.
The report includes two fields to help you map entries back to actual images:

| Field | Example | How to use |
| --- | --- | --- |
| `original_filename` | `IMG_1234.HEIC` | Search by filename in Photos.app |
| `uuid` | `4A2F3D8E-...` | Used by `create-albums.sh` for precise lookup |

Two workflows let you browse photos visually alongside the report:

### Option A — Organised folders (Finder)

`embed-metadata.sh --organize` copies every analysed photo into
`ready-to-submit/submit/`, `ready-to-submit/maybe/`, and `ready-to-submit/skip/`
subfolders. Open the folder in Finder alongside your Markdown report to see
photos and scores side by side.

```bash
./embed-metadata.sh --organize
```

### Option B — Photos.app albums

`create-albums.sh` creates `photo-scout-submit`, `photo-scout-maybe`, and
`photo-scout-skip` albums directly in Photos.app, populated with the analysed
photos. Open Photos.app and browse each album visually.

```bash
./create-albums.sh
```

Photos.app must be open when the script runs. Existing album members are not
duplicated on re-runs. Use `--prefix` to change the album name prefix if
`photo-scout` conflicts with existing albums.

```text
--report FILE    Path to photo-scout JSON report (default: photo-scout-report.json)
--prefix NAME    Album name prefix (default: photo-scout)
--help           Show this help
```

## Embedding metadata and preparing for upload

Once you have a report you're happy with, `embed-metadata.sh` reads it and:

1. Filters photos by recommendation level (`submit`, `maybe`, or `all`)
2. Copies each qualifying original from the Photos library to an output directory
3. Uses `exiftool` to embed the model's keywords and caption into the copy as IPTC and XMP tags

Originals in the Photos library are **never modified** — the script only works on copies.

```bash
./embed-metadata.sh                        # submit recommendations only (default)
./embed-metadata.sh --filter maybe         # submit + maybe
./embed-metadata.sh --organize             # all photos in submit/ maybe/ skip/ subfolders
./embed-metadata.sh --report my-report.json --output ~/Desktop/stock-uploads
```

Full option reference:

```text
--report FILE    Path to photo-scout JSON report (default: photo-scout-report.json)
--output DIR     Output directory for tagged copies (default: ready-to-submit/)
--filter LEVEL   submit | maybe | all (default: submit)
--organize       Create submit/ maybe/ skip/ subfolders (implies --filter all)
--help           Show this help
```

Filter levels:

| Filter | What's included |
| --- | --- |
| `submit` | Only photos the model recommends for submission |
| `maybe` | Submit and maybe recommendations |
| `all` | Every analysed photo, including skip |

The tagged copies in `ready-to-submit/` (or your chosen output directory) are ready to upload
directly to stock platforms. Each file has IPTC keywords, an IPTC caption, and XMP equivalents
embedded — the standard metadata fields that Adobe Stock, Alamy, and Shutterstock read on import.

Run `embed-metadata.sh` from the project directory so it can find the default report and write
the output directory relative to your current location.

## Stock Photo Platforms

Sites that pay per download (unlike Unsplash, which is free):

- [Adobe Stock](https://stock.adobe.com/contributor) — large audience, fair royalties
- [Shutterstock](https://submit.shutterstock.com) — high volume, lower per-download rate
- [Alamy](https://www.alamy.com/contributor/) — accepts editorial/niche content, higher royalties
- [Pond5](https://www.pond5.com) — strong for video, also accepts photos

## License

MIT — see [LICENSE](LICENSE).
