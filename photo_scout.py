#!/usr/bin/env python3
"""photo-scout - Analyse a macOS Photos library for stock photo suitability.

Module:
    photo_scout

Description:
    Enumerates photos from a macOS Photos library using osxphotos, submits each
    image to a locally-running Ollama vision model for analysis, and writes a
    prioritised report of stock photo candidates scored on technical quality and
    commercial appeal.

Author:
    Alister Lewis-Bowen <alister@lewis-bowen.org>

Version:
    0.1.0

Date:
    2026-05-07

License:
    MIT

Usage:
    python photo_scout.py [--library PATH] [--album NAME] [--model MODEL]
                          [--output FILE] [--format csv|markdown]
                          [--limit N] [--since YYYY-MM-DD]

Dependencies:
    - osxphotos >= 0.65
    - ollama >= 0.2.0
    - Ollama running locally with a vision model (default: llava:7b)
      Install model: ollama pull llava:7b

Exit Codes:
    0 - Success
    1 - Invalid arguments, Ollama unreachable, or model unavailable
    2 - Photos library not found or inaccessible
    3 - No photos found matching the given filters
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import ollama
import osxphotos

_ANALYSIS_PROMPT = (
    "Analyse this photograph for stock photo submission suitability. "
    "Respond with ONLY a valid JSON object using exactly this structure, no other text:\n"
    "{\n"
    '  "technical_score": <integer 1-5>,\n'
    '  "commercial_score": <integer 1-5>,\n'
    '  "subject": "<5-10 word description of the main subject>",\n'
    '  "keywords": ["tag1", "tag2", "tag3", "tag4", "tag5"],\n'
    '  "recommendation": "<submit|maybe|skip>",\n'
    '  "reason": "<one sentence explaining the recommendation>"\n'
    "}\n\n"
    "Scoring:\n"
    "  technical_score: 1=very poor (blurry/dark/noisy/crooked), "
    "3=acceptable, 5=excellent (sharp/well-exposed/strong composition).\n"
    "  commercial_score: 1=no stock value (snapshots/personal), "
    "3=moderate appeal, 5=high value (concepts/lifestyle/nature/business/travel).\n"
    "  recommendation: submit=strong candidate, maybe=borderline, skip=not suitable."
)


@dataclass
class PhotoAnalysis:
    """Analysis result for a single photo.

    Attributes:
        filename: Base filename of the photo.
        date_taken: ISO format date string (YYYY-MM-DD).
        original_path: Absolute path to the original file.
        technical_score: Quality rating 1-5 (sharpness, exposure, composition).
        commercial_score: Stock appeal rating 1-5.
        subject: Brief description of main subject.
        keywords: Suggested stock submission tags.
        recommendation: One of 'submit', 'maybe', or 'skip'.
        reason: One-sentence justification for the recommendation.
        error: Non-empty string if analysis failed, empty otherwise.
    """

    filename: str
    date_taken: str
    original_path: str
    technical_score: int = 0
    commercial_score: int = 0
    subject: str = ""
    keywords: list[str] = field(default_factory=list)
    recommendation: str = "skip"
    reason: str = ""
    error: str = ""

    @property
    def overall_score(self) -> float:
        """Mean of technical and commercial scores, or 0.0 if either is missing."""
        if self.technical_score and self.commercial_score:
            return round((self.technical_score + self.commercial_score) / 2, 1)
        return 0.0


def check_ollama(model: str) -> None:
    """Verify Ollama is running and the requested model is available.

    Args:
        model: Ollama model name to check (e.g. 'llava:7b').

    Raises:
        SystemExit: Code 1 if Ollama is unreachable or the model is not pulled.
    """
    try:
        response = ollama.list()
        available = [m.model for m in response.models]
        if not any(model in name for name in available):
            print(f"Error: model '{model}' is not available in Ollama.", file=sys.stderr)
            print(f"Available models: {', '.join(available) or 'none'}", file=sys.stderr)
            print(f"Fix: ollama pull {model}", file=sys.stderr)
            sys.exit(1)
    except ollama.ResponseError as exc:
        print(f"Error: Ollama returned an error: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"Error: cannot connect to Ollama: {exc}", file=sys.stderr)
        print("Fix: ensure Ollama is running — run: ollama serve", file=sys.stderr)
        sys.exit(1)


def load_photos(
    library_path: Path | None,
    album: str | None,
    since: datetime | None,
    limit: int | None,
) -> tuple[list[osxphotos.PhotoInfo], int]:
    """Load photos from the macOS Photos library, filtering as requested.

    Photos without a locally-present original file (e.g. iCloud-only) are
    silently excluded; the count of skipped photos is returned so callers
    can inform the user.

    Args:
        library_path: Path to the Photos library package. Uses the system
            default library when None.
        album: Filter to photos whose album title matches this string exactly.
            Processes all albums when None.
        since: Return only photos taken on or after this date. No date filter
            when None.
        limit: Cap the result at this many photos. No cap when None.

    Returns:
        Tuple of (photos_to_process, skipped_cloud_count).

    Raises:
        SystemExit: Code 2 if the library cannot be opened.
        SystemExit: Code 3 if no photos match the given filters.
    """
    try:
        kwargs: dict = {"dbfile": str(library_path)} if library_path else {}
        db = osxphotos.PhotosDB(**kwargs)
    except Exception as exc:
        print(f"Error: cannot open Photos library: {exc}", file=sys.stderr)
        print(
            "Fix: check the path and ensure Photos.app is not performing a library operation.",
            file=sys.stderr,
        )
        sys.exit(2)

    photos = db.photos()

    if album:
        photos = [p for p in photos if any(a.title == album for a in p.album_info)]

    if since:
        since_date = since.date()
        photos = [p for p in photos if p.date and p.date.date() >= since_date]

    available = [p for p in photos if p.path and Path(p.path).exists()]
    skipped_cloud = len(photos) - len(available)

    if not available:
        print("Error: no photos with local originals found matching the given filters.", file=sys.stderr)
        if skipped_cloud:
            print(
                f"Note: {skipped_cloud} photo(s) are iCloud-only and were excluded. "
                "Download them in Photos.app first.",
                file=sys.stderr,
            )
        sys.exit(3)

    if limit:
        available = available[:limit]

    return available, skipped_cloud


def _extract_json(text: str) -> dict:
    """Extract a JSON object from a string, tolerating model preamble or trailing text.

    Args:
        text: Raw string response from the model.

    Returns:
        Parsed dict, or empty dict if no valid JSON object is found.
    """
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass

    return {}


def analyse_photo(photo: osxphotos.PhotoInfo, model: str) -> PhotoAnalysis:
    """Analyse a single photo using the Ollama vision model.

    Args:
        photo: osxphotos PhotoInfo object representing the photo.
        model: Ollama model name to use for inference.

    Returns:
        Populated PhotoAnalysis dataclass. On failure the error field is set
        and scores default to 0.
    """
    path = Path(photo.path)
    result = PhotoAnalysis(
        filename=path.name,
        date_taken=photo.date.strftime("%Y-%m-%d") if photo.date else "",
        original_path=str(path),
    )

    try:
        response = ollama.chat(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": _ANALYSIS_PROMPT,
                    "images": [str(path)],
                }
            ],
        )
        parsed = _extract_json(response.message.content)
        if parsed:
            result.technical_score = max(1, min(5, int(parsed.get("technical_score", 0))))
            result.commercial_score = max(1, min(5, int(parsed.get("commercial_score", 0))))
            result.subject = str(parsed.get("subject", ""))
            result.keywords = [str(k) for k in parsed.get("keywords", [])]
            rec = str(parsed.get("recommendation", "skip")).lower()
            result.recommendation = rec if rec in ("submit", "maybe", "skip") else "skip"
            result.reason = str(parsed.get("reason", ""))
        else:
            result.error = "Could not parse model response as JSON"
    except Exception as exc:
        result.error = str(exc)

    return result


def write_json(analyses: list[PhotoAnalysis], output_path: Path) -> None:
    """Write analysis results to a JSON file.

    Args:
        analyses: List of PhotoAnalysis results (caller should pre-sort).
        output_path: Destination file path; will be created or overwritten.
    """
    data = [
        {
            "filename": a.filename,
            "date_taken": a.date_taken,
            "original_path": a.original_path,
            "overall_score": a.overall_score,
            "technical_score": a.technical_score,
            "commercial_score": a.commercial_score,
            "recommendation": a.recommendation,
            "subject": a.subject,
            "keywords": a.keywords,
            "reason": a.reason,
            "error": a.error,
        }
        for a in analyses
    ]
    output_path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def write_csv(analyses: list[PhotoAnalysis], output_path: Path) -> None:
    """Write analysis results to a CSV file.

    Args:
        analyses: List of PhotoAnalysis results (caller should pre-sort).
        output_path: Destination file path; will be created or overwritten.
    """
    fieldnames = [
        "overall_score",
        "recommendation",
        "technical_score",
        "commercial_score",
        "subject",
        "keywords",
        "reason",
        "filename",
        "date_taken",
        "original_path",
        "error",
    ]
    with output_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for a in analyses:
            writer.writerow(
                {
                    "overall_score": a.overall_score,
                    "recommendation": a.recommendation,
                    "technical_score": a.technical_score,
                    "commercial_score": a.commercial_score,
                    "subject": a.subject,
                    "keywords": "; ".join(a.keywords),
                    "reason": a.reason,
                    "filename": a.filename,
                    "date_taken": a.date_taken,
                    "original_path": a.original_path,
                    "error": a.error,
                }
            )


def write_markdown(analyses: list[PhotoAnalysis], output_path: Path) -> None:
    """Write analysis results to a Markdown file.

    Args:
        analyses: List of PhotoAnalysis results (caller should pre-sort).
        output_path: Destination file path; will be created or overwritten.
    """
    submit = [a for a in analyses if a.recommendation == "submit"]
    maybe = [a for a in analyses if a.recommendation == "maybe"]
    errors = [a for a in analyses if a.error]

    lines: list[str] = [
        "# Photo Scout Report",
        "",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}  ",
        f"Analysed: {len(analyses)} | "
        f"Submit: {len(submit)} | "
        f"Maybe: {len(maybe)} | "
        f"Skip: {len(analyses) - len(submit) - len(maybe)}",
        "",
    ]

    for title, items in [("Submit", submit), ("Maybe", maybe)]:
        if not items:
            continue
        lines += [
            f"## {title}",
            "",
            "| Score | File | Subject | Keywords | Reason |",
            "|---|---|---|---|---|",
        ]
        for a in items:
            kw = ", ".join(a.keywords[:5])
            lines.append(f"| {a.overall_score} | {a.filename} | {a.subject} | {kw} | {a.reason} |")
        lines.append("")

    if errors:
        lines += ["## Errors", ""]
        for a in errors:
            lines.append(f"- `{a.filename}`: {a.error}")
        lines.append("")

    output_path.write_text("\n".join(lines), encoding="utf-8")


def _sort_key(a: PhotoAnalysis) -> tuple:
    priority = {"submit": 0, "maybe": 1, "skip": 2}
    return (-a.overall_score, priority.get(a.recommendation, 3))


def main() -> None:
    """Entry point: parse arguments, run analysis pipeline, write report."""
    parser = argparse.ArgumentParser(
        description="Analyse a macOS Photos library for stock photo suitability.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python photo_scout.py\n"
            "  python photo_scout.py --album Landscapes --limit 50\n"
            "  python photo_scout.py --since 2024-01-01 --format markdown\n"
            "  python photo_scout.py --model moondream --output quick-check.csv\n\n"
            "Vision models to try (pull with 'ollama pull <name>'):\n"
            "  moondream      ~1.7 GB  fastest on low-RAM hardware\n"
            "  llava:7b       ~4.1 GB  recommended quality/speed balance\n"
            "  llava-phi3     ~2.9 GB  good middle ground\n"
        ),
    )
    parser.add_argument(
        "--library",
        metavar="PATH",
        type=Path,
        help="Path to Photos library package (default: system default library)",
    )
    parser.add_argument(
        "--album",
        metavar="NAME",
        help="Filter to photos in this album (exact title match)",
    )
    parser.add_argument(
        "--model",
        default="llava:7b",
        metavar="MODEL",
        help="Ollama vision model to use (default: llava:7b)",
    )
    parser.add_argument(
        "--output",
        default="photo-scout-report.json",
        metavar="FILE",
        type=Path,
        help="Output file path (default: photo-scout-report.json)",
    )
    parser.add_argument(
        "--format",
        choices=["json", "csv", "markdown"],
        default="json",
        dest="output_format",
        help="Output format (default: json)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        metavar="N",
        help="Maximum number of photos to process (useful for testing)",
    )
    parser.add_argument(
        "--since",
        metavar="YYYY-MM-DD",
        help="Only process photos taken on or after this date",
    )
    args = parser.parse_args()

    since: datetime | None = None
    if args.since:
        try:
            since = datetime.strptime(args.since, "%Y-%m-%d")
        except ValueError:
            print(f"Error: invalid date '{args.since}' — use YYYY-MM-DD format.", file=sys.stderr)
            sys.exit(1)

    output_path: Path = args.output
    suffix_map = {"json": ".json", "markdown": ".md"}
    expected_suffix = suffix_map.get(args.output_format)
    if expected_suffix and output_path.suffix != expected_suffix:
        output_path = output_path.with_suffix(expected_suffix)

    print(f"Checking Ollama (model: {args.model})...")
    check_ollama(args.model)

    print("Opening Photos library...")
    photos, skipped_cloud = load_photos(args.library, args.album, since, args.limit)

    if skipped_cloud:
        print(f"  Note: {skipped_cloud} iCloud-only photo(s) skipped (not downloaded locally).")

    print(f"Analysing {len(photos)} photo(s). This may take a while on low-RAM hardware.\n")

    analyses: list[PhotoAnalysis] = []
    for i, photo in enumerate(photos, 1):
        name = Path(photo.path).name
        print(f"  [{i:>{len(str(len(photos)))}}/{len(photos)}] {name}", end="", flush=True)
        result = analyse_photo(photo, args.model)
        if result.error:
            print(f"  — ERROR: {result.error}")
        else:
            print(f"  — {result.recommendation} (score {result.overall_score})")
        analyses.append(result)

    analyses.sort(key=_sort_key)

    print()
    if args.output_format == "json":
        write_json(analyses, output_path)
    elif args.output_format == "csv":
        write_csv(analyses, output_path)
    else:
        write_markdown(analyses, output_path)

    submit_count = sum(1 for a in analyses if a.recommendation == "submit")
    maybe_count = sum(1 for a in analyses if a.recommendation == "maybe")
    error_count = sum(1 for a in analyses if a.error)
    skip_count = len(analyses) - submit_count - maybe_count

    print(f"Done. {len(analyses)} photo(s) analysed.")
    print(f"  Submit: {submit_count}  |  Maybe: {maybe_count}  |  Skip: {skip_count}")
    if error_count:
        print(f"  Errors: {error_count} (see report for details)")
    print(f"\nReport: {output_path.resolve()}")


if __name__ == "__main__":
    main()
