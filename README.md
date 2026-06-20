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

## End-to-end workflow

### Step 1 — First-time setup

```bash
git clone https://github.com/ali5ter/photo-scout.git
cd photo-scout
./setup.sh
source .venv/bin/activate
```

Build the CLIP reference set (takes ~5 min — downloads 200 Wikimedia featured photos):

```bash
python build_reference.py --download 200
```

Optionally supplement with your own known-good stock photos to improve CLIP scoring for
your subjects:

```bash
python build_reference.py --source ~/path/to/good-stock-photos --download 100
```

### Step 2 — Curate candidates in Photos.app

Create an album called **Stock Candidates** (or any name) in Photos.app and drag in photos
you think have potential. This pre-filters the analysis to photos worth scoring.

If those photos are iCloud-only (Photos shows a cloud icon), download them first:

```bash
osxphotos export /tmp/stock-dl --album "Stock Candidates" \
  --download-missing --use-photokit
```

This forces Photos to pull the originals to disk. Run the analysis immediately after —
iCloud will re-offload them automatically when storage is needed.

### Step 3 — Analyse

```bash
python photo_scout.py --album "Stock Candidates"
```

Subsequent runs skip already-analysed photos. Use `--force` to re-score everything:

```bash
python photo_scout.py --album "Stock Candidates" --force
```

### Step 4 — Review results

Open the Markdown report for a human-readable summary (re-run with `--markdown` to generate it):

```bash
python photo_scout.py --album "Stock Candidates" --markdown
open photo-scout-report.md
```

Or tag photos directly in Photos.app for visual browsing:

```bash
./tag-photos.sh
```

Then search for `photo-scout-submit` in Photos.app, or create a Smart Album with that keyword.

See [STOCK-PHOTO-RULES.md](STOCK-PHOTO-RULES.md) before submitting — covers model/property
releases, copyright, and logo rules.

### Step 5 — Prepare for upload

Copy recommended photos with IPTC/XMP metadata embedded:

```bash
./embed-metadata.sh                  # submit recommendations only
./embed-metadata.sh --filter maybe   # submit + maybe
./embed-metadata.sh --organize       # all photos in submit/ maybe/ skip/ subfolders
```

Tagged copies land in `ready-to-submit/`. Upload directly to Adobe Stock, Shutterstock,
Alamy, or Pond5.

---

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

# Use CLIP stock-likeness scoring (requires clip-reference-embeddings.npy)
python photo_scout.py --clip-reference clip-reference-embeddings.npy

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
--clip-reference FILE  Path to CLIP reference embeddings (from build_reference.py)
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

Each photo receives two scores on a 1–5 scale:

`technical_score` is computed deterministically from the image using PIL/NumPy — no model
involved. It combines sharpness (90th-percentile edge magnitude), exposure (clipping, brightness,
dynamic range), and noise (flat-region variance), weighted 50/30/20%.

`commercial_score` comes from the Ollama vision model, which assesses market demand, concept
clarity, and licensing suitability.

| Score | technical_score | commercial_score |
| --- | --- | --- |
| 1 | Blurry, badly exposed, or unusable | Identifiable people without releases, copyrighted elements, or no commercial use |
| 2 | Noticeably soft, poorly exposed, or distracting noise | Personal snapshot, documentary, or tourist shot unlikely to sell |
| 3 | Acceptable but soft, slightly off-exposure, or minor noise — borderline | Niche or generic subject with limited buyers |
| 4 | Sharp and clean — meets stock site technical bar | Solid commercial appeal with an identifiable buyer market |
| 5 | Tack sharp, perfect exposure, no visible noise, professional composition | Strong sellable concept — business, lifestyle, nature, travel, technology |

`overall_score` is the mean of `technical_score` and the effective commercial signal (1.0–5.0).
When `--clip-reference` is active and `clip_score ≥ 2.5`, `clip_score` replaces `commercial_score`
as the commercial signal; otherwise `commercial_score` is used. The 2.5 floor prevents near-floor
CLIP scores (which reflect reference set gaps rather than real similarity) from overriding a strong
`commercial_score`.

`recommendation` is determined by strict score thresholds:

- **submit** — both `technical_score` and the commercial signal are 4 or above
- **maybe** — both scores are 3 or above, but not both 4+; review manually before deciding
- **skip** — either score is below 3, or the photo fails stock site standards

Thresholds are enforced in code, so a `submit` can never appear with scores below 4/4
regardless of what the model says.

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
- **Download selectively via osxphotos** (works on macOS 26+ where right-click download is absent):

```bash
mkdir -p /tmp/photos-export
osxphotos export /tmp/photos-export \
  --album "Album Name" \
  --download-missing \
  --use-photokit \
  --limit 20
```

  Run `photo_scout.py` immediately after — iCloud re-offloads photos on its own schedule
  if **Optimise Mac Storage** is enabled.

## Identifying photos from the report

Photos.app stores originals under internal UUID-based filenames (e.g.
`4A2F3D8E-1234-5678-ABCD-9012EFAB.HEIC`) that are not searchable in Photos.app.
The report includes two fields to help you map entries back to actual images:

| Field | Example | How to use |
| --- | --- | --- |
| `original_filename` | `IMG_1234.HEIC` | Search by filename in Photos.app |
| `uuid` | `4A2F3D8E-...` | Used by `tag-photos.sh` for precise Photos.app lookup |

Two workflows let you browse photos visually alongside the report:

### Option A — Organised folders (Finder)

`embed-metadata.sh --organize` copies every analysed photo into
`ready-to-submit/submit/`, `ready-to-submit/maybe/`, and `ready-to-submit/skip/`
subfolders. Open the folder in Finder alongside your Markdown report to see
photos and scores side by side.

```bash
./embed-metadata.sh --organize
```

### Option B — Photos.app keyword search

`tag-photos.sh` sets a Photos.app keyword on each photo matching its
recommendation: `photo-scout-submit`, `photo-scout-maybe`, or `photo-scout-skip`.
Once tagged, open Photos.app and search for a keyword to browse the photos
visually — or create a persistent Smart Album.

```bash
./tag-photos.sh
```

Photos.app must be open when the script runs. Existing user keywords on photos
are preserved; re-running updates the keyword without duplicating it. Use
`--clear` to remove all photo-scout keywords.

```text
--report FILE    Path to photo-scout JSON report (default: photo-scout-report.json)
--prefix NAME    Keyword prefix (default: photo-scout)
--clear          Remove all photo-scout keywords without re-tagging
--help           Show this help
```

To browse after tagging:

1. Open Photos.app and type `photo-scout-submit` in the search bar — all
   submit candidates appear visually.
2. For a permanent sidebar view: **File → New Smart Album**, set the filter to
   **Keyword contains `photo-scout-submit`**, and save. Repeat for `maybe` and
   `skip` if wanted.

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

## CLIP Stock-Likeness Scoring (optional)

The Ollama vision model scores commercial appeal by reasoning about the image, but a small model
like `llava:7b` has limited commercial intuition. As an alternative, `build_reference.py` builds
a CLIP embedding set from photos that are known to meet stock photo standards — then
`photo_scout.py --clip-reference` scores each new photo by similarity to that reference set.

**What CLIP scoring does:**

- Compares your photo to a reference set of accepted stock-quality images using cosine similarity
- Produces a `clip_score` (1–5) that reflects how closely the photo's subject and style match
  the reference set
- When `--clip-reference` is provided, `clip_score` replaces the model's `commercial_score` for
  recommendation thresholds and `overall_score` — the vision model still runs for `technical_score`,
  `subject`, `keywords`, and `reason`

**What CLIP scoring cannot do:**

- It measures *subject/style similarity*, not technical quality (that remains `technical_score`)
- A great photo of a rare subject may score low if the reference set has few similar examples
- It is not a replacement for human review of borderline cases

### Step 1 — Build the reference set

Download 200 featured photos from Wikimedia Commons and compute embeddings:

```bash
python build_reference.py --download 200
```

Optionally add your own known-good stock photos:

```bash
python build_reference.py --source ~/my-reference-photos --download 100
```

Full option reference:

```text
--source DIR         Local directory of reference photos to embed
--download N         Download N featured photos from Wikimedia Commons (no API key needed)
--output FILE        Output .npy file (default: clip-reference-embeddings.npy)
--download-dir DIR   Cache for downloaded photos (default: clip-reference/)
```

Wikimedia Commons [Featured Pictures](https://commons.wikimedia.org/wiki/Commons:Featured_pictures)
are hand-curated by Wikipedia editors for technical quality — a useful proxy for stock photo
standards, freely available with no authentication required.

### Step 2 — Score with CLIP

Pass the embeddings file when running `photo_scout.py`:

```bash
python photo_scout.py --clip-reference clip-reference-embeddings.npy
```

The per-photo output line gains a `clip:` field:

```text
[1/10] IMG_1234.HEIC  — submit  tech:4  comm:3  clip:4.2  78%
```

The JSON report gains a `clip_score` field alongside `commercial_score`. Use `--force` to re-score
photos already in the report with the new CLIP signal.

### Reference set size recommendations

| Reference photos | Accuracy | Notes |
| --- | --- | --- |
| 50–100 | Low | Useful for basic filtering |
| 200–500 | Good | Recommended starting point |
| 1000+ | Best | More diverse coverage of subjects |

## Stock Photo Platforms

Sites that pay per download (unlike Unsplash, which is free):

- [Adobe Stock](https://stock.adobe.com/contributor) — large audience, fair royalties
- [Shutterstock](https://submit.shutterstock.com) — high volume, lower per-download rate
- [Alamy](https://www.alamy.com/contributor/) — accepts editorial/niche content, higher royalties
- [Pond5](https://www.pond5.com) — strong for video, also accepts photos

## Additional Documentation

- [HOW-IT-WORKS.md](HOW-IT-WORKS.md) — technical deep-dive with pipeline flow diagrams covering
  scoring architecture, CLIP theory, and post-processing workflows
- [STOCK-PHOTO-RULES.md](STOCK-PHOTO-RULES.md) — practical reference for model/property releases,
  architectural copyright, logos/trademarks, and editorial vs commercial licences

## License

MIT — see [LICENSE](LICENSE).
