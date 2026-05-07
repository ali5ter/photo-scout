# photo-scout

A command-line tool that analyses a macOS Photos library for stock photo suitability using a
locally-running Ollama vision model. Photos are scored on technical quality and commercial appeal,
producing a prioritised report of submission candidates.

Everything runs locally — no cloud services, no API costs, no data leaving the machine.

## Features

- Reads directly from the macOS Photos library (no export step needed)
- Filters by album, date range, or photo count
- Scores each photo on technical quality and commercial appeal (1–5 each)
- Recommends: **submit**, **maybe**, or **skip**
- Outputs a CSV (sortable in Numbers/Excel) or a Markdown report
- Handles iCloud-only photos gracefully (skips with a count)

## Platform Support

| Platform | Ollama install method |
|---|---|
| macOS (Intel / Apple Silicon) | `brew install ollama` (via Homebrew) |
| Ubuntu / Debian / Raspberry Pi | Official install script via `curl` |

`setup.sh` handles installation automatically on both platforms.

> **Raspberry Pi note:** Vision models are memory-intensive. On a Pi 4/5 with 4 GB RAM,
> only `moondream` (~1.7 GB) is practical — `llava:7b` (4.1 GB) will OOM or run
> impractically slowly. Even `moondream` will take several minutes per image on Pi hardware.

## Requirements

- macOS with Photos.app (the Photos library is only accessible on macOS)
- Python 3.10+
- Ollama — installed automatically by `setup.sh` if absent

## Installation

```bash
git clone --recurse-submodules https://github.com/ali5ter/photo-scout.git
cd photo-scout
./setup.sh
source .venv/bin/activate
```

`setup.sh` is idempotent — safe to re-run at any time. It:

1. Installs Ollama if not already present (platform-appropriate method)
2. Starts the Ollama service if not running
3. Pulls the vision model if not already downloaded
4. Creates a Python virtual environment
5. Installs pip dependencies

To use a different model at setup time:

```bash
./setup.sh moondream    # faster, smaller — good for testing or Raspberry Pi
./setup.sh llava-phi3   # good middle ground
./setup.sh llava:7b     # default, best quality (needs ~4 GB free RAM)
```

## Model Choice

No vision models are bundled — `setup.sh` pulls one automatically.

| Model | Size | Speed on M1 8 GB | Quality | RPi suitable? |
|---|---|---|---|---|
| `moondream` | 1.7 GB | Fast (~5–10 s/image) | Basic descriptions | Yes (slow) |
| `llava-phi3` | 2.9 GB | Moderate (~15–30 s/image) | Good | Borderline |
| `llava:7b` | 4.1 GB | Slow (~30–60 s/image) | Best (recommended) | No |

On an M1 8 GB machine `llava:7b` fits comfortably (unified memory). It is slow but accurate.
Use `--model moondream` for a quick test run.

## Usage

```bash
# Analyse the default Photos library (all photos with local originals)
python photo_scout.py

# Filter to a specific album
python photo_scout.py --album Landscapes

# Only photos taken since a date, output as Markdown
python photo_scout.py --since 2024-01-01 --format markdown

# Quick test: process 20 photos with a faster model
python photo_scout.py --model moondream --limit 20

# Use a non-default library path
python photo_scout.py --library "/Volumes/External/My Library.photoslibrary"
```

Full option reference:

```text
--library PATH     Path to Photos library (default: system default)
--album NAME       Filter to photos in this album (exact title match)
--model MODEL      Ollama model (default: llava:7b)
--output FILE      Output file (default: photo-scout-report.csv)
--format FORMAT    csv or markdown (default: csv)
--limit N          Max photos to process
--since YYYY-MM-DD Only photos taken on or after this date
```

## Output

### CSV

The default CSV output has one row per photo, sorted by overall score descending:

| Column | Description |
|---|---|
| `overall_score` | Mean of technical and commercial scores (1.0–5.0) |
| `recommendation` | `submit`, `maybe`, or `skip` |
| `technical_score` | Sharpness, exposure, composition (1–5) |
| `commercial_score` | Stock appeal — concept, lifestyle, nature (1–5) |
| `subject` | Brief description of the main subject |
| `keywords` | Semicolon-separated suggested tags |
| `reason` | One-sentence justification |
| `filename` | Original filename |
| `date_taken` | Date photo was taken |
| `original_path` | Full path to the original file |

### Markdown

Use `--format markdown` for a human-readable report grouped into **Submit**, **Maybe**, and
**Skip** sections.

## iCloud Photos

Photos stored exclusively in iCloud (not downloaded to the local library) are skipped
automatically. To include them, open Photos.app, select the photos, and choose
**Image > Download Originals**.

## Stock Photo Platforms

Sites that pay per download (unlike Unsplash, which is free):

- [Adobe Stock](https://stock.adobe.com/uk/contributor) — large audience, fair royalties
- [Shutterstock](https://submit.shutterstock.com) — high volume, lower per-download rate
- [Alamy](https://www.alamy.com/contributor/) — accepts editorial/niche content, higher royalties
- [Pond5](https://www.pond5.com) — strong for video, also accepts photos

## License

MIT — see [LICENSE](LICENSE).
