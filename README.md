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

## Requirements

- macOS with Photos.app
- [Ollama](https://ollama.com) installed and running (`ollama serve`)
- A vision-capable Ollama model (see [Model Choice](#model-choice) below)
- Python 3.10+

## Installation

```bash
# Clone the repository
git clone https://github.com/ali5ter/photo-scout.git
cd photo-scout

# Create a virtual environment and install dependencies
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Model Choice

No vision models are bundled — pull one before running:

| Model | Size | Speed on M1 8 GB | Quality |
|---|---|---|---|
| `moondream` | 1.7 GB | Fast (~5–10 s/image) | Basic descriptions |
| `llava-phi3` | 2.9 GB | Moderate (~15–30 s/image) | Good |
| `llava:7b` | 4.1 GB | Slow (~30–60 s/image) | Best (recommended) |

```bash
ollama pull llava:7b
```

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
