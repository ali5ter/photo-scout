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
- Outputs JSON (default), CSV, or Markdown
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

| Model | Size | Speed on M1 8 GB | Quality |
|---|---|---|---|
| `moondream` | 1.7 GB | Fast (~5–10 s/image) | Pipeline testing only — too small for real analysis |
| `llava-phi3` | 2.9 GB | Moderate (~15–30 s/image) | Good |
| `llava:7b` | 4.1 GB | Slow (~30–60 s/image) | Best (recommended) |

On an M1 8 GB machine `llava:7b` fits comfortably (unified memory). It is slow but accurate.

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

# Output as CSV (for sorting in Numbers/Excel)
python photo_scout.py --format csv
```

Full option reference:

```text
--library PATH     Path to Photos library (default: system default)
--album NAME       Filter to photos in this album (exact title match)
--model MODEL      Ollama model (default: llava:7b)
--output FILE      Output file written to current directory (default: photo-scout-report.json)
--format FORMAT    json | csv | markdown (default: json)
--limit N          Max photos to process (selects most recent N)
--since YYYY-MM-DD Only photos taken on or after this date
--force            Re-analyse all photos, ignoring any existing report
```

## Output files

All output is written to the **current working directory** — wherever you run the script from.
Running from the project directory (`photo-scout/`) is recommended so the report is alongside
the other scripts.

| File | Created by | Description |
|---|---|---|
| `photo-scout-report.json` | `photo_scout.py` | Analysis results (gitignored) |
| `ready-to-submit/` | `embed-metadata.sh` | Tagged copies of recommended photos (gitignored) |

## Understanding the scores

Each photo receives two scores from the vision model, both on a 1–5 scale:

| Score | technical_score | commercial_score |
|---|---|---|
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

## Embedding metadata and preparing for upload

After reviewing the report, use `embed-metadata.sh` to copy qualifying photos to
`ready-to-submit/` with IPTC/XMP keywords and caption embedded:

```bash
./embed-metadata.sh                        # submit recommendations only (default)
./embed-metadata.sh --filter maybe         # submit + maybe
./embed-metadata.sh --report my-report.json --output ~/Desktop/stock-uploads
```

## Stock Photo Platforms

Sites that pay per download (unlike Unsplash, which is free):

- [Adobe Stock](https://stock.adobe.com/uk/contributor) — large audience, fair royalties
- [Shutterstock](https://submit.shutterstock.com) — high volume, lower per-download rate
- [Alamy](https://www.alamy.com/contributor/) — accepts editorial/niche content, higher royalties
- [Pond5](https://www.pond5.com) — strong for video, also accepts photos

## License

MIT — see [LICENSE](LICENSE).
