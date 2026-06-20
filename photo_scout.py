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
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import ollama
import osxphotos
from rich.console import Console

console = Console()
err_console = Console(stderr=True)

_ANALYSIS_PROMPT = (
    "You are a senior stock photo editor at a premium agency. Technical quality "
    "(sharpness, exposure, noise) is assessed separately by image analysis tools — "
    "focus entirely on commercial appeal and content.\n\n"
    "Analyse this photograph and respond with ONLY a JSON object, no other text.\n\n"
    "JSON structure:\n"
    "{\n"
    '  "commercial_score": <integer 1-5>,\n'
    '  "subject": <string: 5-10 words describing what is in the photo>,\n'
    '  "keywords": <array of 5-10 single-word or short descriptive tags>,\n'
    '  "recommendation": <one of the strings: submit, maybe, skip>,\n'
    '  "reason": <string: one sentence justifying the recommendation>\n'
    "}\n\n"
    "Commercial score — market demand, concept clarity, licensing:\n"
    "  5 = Strong, clearly sellable concept (business, lifestyle, nature, travel, technology)\n"
    "  4 = Solid commercial appeal with an identifiable buyer market\n"
    "  3 = Niche or generic — limited buyers\n"
    "  2 = Personal, documentary, or tourist snapshot — unlikely to sell\n"
    "  1 = Identifiable people without releases, copyrighted elements, or zero commercial use\n\n"
    "Recommendation — base on commercial_score only:\n"
    "  submit = commercial_score >= 4\n"
    "  maybe  = commercial_score >= 3\n"
    "  skip   = commercial_score < 3, or identifiable people without releases, "
    "copyrighted logos/brands, or clearly personal photos with no commercial application"
)

# Technical quality scoring calibration constants (tune if scores feel uniformly high or low).
# Measured on a 1500px-wide grayscale image using PIL FIND_EDGES (Laplacian-like).
# Sharpness uses the 90th-percentile edge value (not mean) to focus on strong edges
# and reduce sensitivity to smooth areas (sky, skin) that drag the mean down.
_SHARP_EDGE_LOW = 3.0    # p90 edge magnitude → sharpness 1 (blurry)
_SHARP_EDGE_HIGH = 35.0  # p90 edge magnitude → sharpness 5 (very sharp)
# 35 calibrated against iPhone HEIC originals: p90 typically 25-60 for well-focused shots,
# so H=35 spreads scores meaningfully (p90=25→sharp≈4.4, p90=35→5) rather than bunching at 4.
_NOISE_FLAT_HIGH = 6.0   # mean diff in flat regions → noise component 1 (very noisy)

# CLIP stock-likeness scoring — must match _CLIP_MODEL / _CLIP_PRETRAINED in build_reference.py
_CLIP_MODEL = "ViT-B-32"
_CLIP_PRETRAINED = "openai"
# Cosine similarity thresholds calibrated for ViT-B-32/openai against Wikimedia featured photos.
# mean-of-top-50 similarities typically fall in [0.65, 0.88] for natural images.
_SIM_LOW = 0.65   # → clip_score 1
_SIM_HIGH = 0.88  # → clip_score 5
_TOP_K = 50       # number of top reference matches to average
# Below this clip_score, the reference set likely doesn't cover the photo's subject;
# fall back to the model's commercial_score rather than penalising with a floor value.
# 2.5 avoids treating near-floor CLIP scores (2.0–2.4) as meaningful signal — scores
# in that range are noise from Wikimedia's wildlife/architecture bias, not real similarity.
_CLIP_MIN_SIGNAL = 2.5


@dataclass
class PhotoAnalysis:
    """Analysis result for a single photo.

    Attributes:
        filename: Internal UUID-based filename used by Photos.app.
        original_filename: Camera-assigned filename at import (e.g. IMG_1234.HEIC).
        uuid: Photos.app UUID (used for album operations via osascript).
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
    original_filename: str
    uuid: str
    date_taken: str
    original_path: str
    technical_score: int = 0
    commercial_score: int = 0
    subject: str = ""
    keywords: list[str] = field(default_factory=list)
    recommendation: str = "skip"
    reason: str = ""
    clip_score: float = 0.0
    error: str = ""

    @property
    def overall_score(self) -> float:
        """Mean of technical score and the best available commercial signal.

        Uses clip_score when present (CLIP-based stock similarity), otherwise
        falls back to commercial_score (model judgment). Returns 0.0 if either
        component is missing.
        """
        if not self.technical_score:
            return 0.0
        commercial = (
            self.clip_score
            if self.clip_score >= _CLIP_MIN_SIGNAL
            else float(self.commercial_score)
        )
        if not commercial:
            return 0.0
        return round((self.technical_score + commercial) / 2, 1)


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
            err_console.print(f"[red bold]Error:[/] model '{model}' is not available in Ollama.")
            err_console.print(f"Available models: {', '.join(available) or 'none'}")
            err_console.print(f"Fix: [cyan]ollama pull {model}[/]")
            sys.exit(1)
    except ollama.ResponseError as exc:
        err_console.print(f"[red bold]Error:[/] Ollama returned an error: {exc}")
        sys.exit(1)
    except Exception as exc:
        err_console.print(f"[red bold]Error:[/] cannot connect to Ollama: {exc}")
        err_console.print("Fix: [cyan]ollama serve[/]")
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
        err_console.print(f"[red bold]Error:[/] cannot open Photos library: {exc}")
        err_console.print("Fix: check the path and ensure Photos.app is not performing a library operation.")
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
        err_console.print("[red bold]Error:[/] no photos with local originals found matching the given filters.")
        if skipped_cloud:
            err_console.print(
                f"[yellow]Note:[/] {skipped_cloud} photo(s) are iCloud-only and were excluded. "
                "Download them in Photos.app first."
            )
        sys.exit(3)

    # Sort newest-first so --limit picks the most recent unanalysed photos
    available.sort(key=lambda p: p.date.timestamp() if p.date else 0, reverse=True)

    return available, skipped_cloud


def _load_prior_results(output_path: Path) -> dict[str, dict]:
    """Load previously analysed results from an existing JSON report.

    Args:
        output_path: Path to an existing photo-scout JSON report.

    Returns:
        Dict mapping original_path to its result dict, or empty dict if the
        file does not exist or cannot be parsed.
    """
    if not output_path.exists():
        return {}
    try:
        data = json.loads(output_path.read_text(encoding="utf-8"))
        return {item["original_path"]: item for item in data if isinstance(item, dict)}
    except (json.JSONDecodeError, KeyError, TypeError):
        return {}


def _analysis_from_dict(data: dict) -> PhotoAnalysis:
    """Reconstruct a PhotoAnalysis from a JSON-decoded dict.

    Args:
        data: Dict as written by write_json.

    Returns:
        PhotoAnalysis with fields populated from the dict.
    """
    return PhotoAnalysis(
        filename=data.get("filename", ""),
        original_filename=data.get("original_filename", data.get("filename", "")),
        uuid=data.get("uuid", Path(data.get("filename", "")).stem),
        date_taken=data.get("date_taken", ""),
        original_path=data.get("original_path", ""),
        technical_score=int(data.get("technical_score", 0)),
        commercial_score=int(data.get("commercial_score", 0)),
        subject=data.get("subject", ""),
        keywords=list(data.get("keywords", [])),
        recommendation=data.get("recommendation", "skip"),
        reason=data.get("reason", ""),
        clip_score=float(data.get("clip_score", 0.0)),
        error=data.get("error", ""),
    )


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


def _compute_technical_score(image_path: Path) -> int:
    """Compute a technical quality score from deterministic image analysis.

    Combines three independent signals, each mapped to a 1–5 component score:
    - Sharpness: mean edge magnitude via PIL FIND_EDGES (Laplacian-like filter)
    - Exposure: histogram clipping fraction, mean brightness, and dynamic range
    - Noise: pixel-level variance in flat (low-edge) regions

    Weights: sharpness 50 %, exposure 30 %, noise 20 %.
    All metrics are computed at a standard 1500 px width so scores are
    comparable across photos of different original resolutions.

    Args:
        image_path: Path to a JPEG or PNG image.

    Returns:
        Technical score 1–5, or 0 if analysis fails (caller falls back to model score).
    """
    try:
        import math

        import numpy as np
        from PIL import Image, ImageFilter

        img = Image.open(image_path).convert("RGB")
        w, h = img.size
        if w > 1500:
            img = img.resize((1500, round(h * 1500 / w)), Image.LANCZOS)

        gray = img.convert("L")
        gray_arr = np.array(gray, dtype=float)

        # Sharpness — 90th-percentile edge response (log scale to spread the 1–5 range).
        # p90 focuses on the strongest edges in the frame rather than the mean, which is
        # suppressed by large smooth areas (sky, skin, backgrounds).
        edges_arr = np.array(gray.filter(ImageFilter.FIND_EDGES), dtype=float)
        p90_edge = max(float(np.percentile(edges_arr, 90)), 0.5)
        sharp = 1.0 + 4.0 * (
            math.log(p90_edge / _SHARP_EDGE_LOW)
            / math.log(_SHARP_EDGE_HIGH / _SHARP_EDGE_LOW)
        )
        sharp = max(1.0, min(5.0, sharp))

        # Exposure — clipping, brightness, dynamic range
        clipped_frac = float(np.mean((gray_arr < 8) | (gray_arr > 247)))
        mean_brightness = float(gray_arr.mean())
        std_brightness = float(gray_arr.std())

        clipping_ok = max(0.0, 1.0 - clipped_frac * 8)         # 12.5 % clip → 0
        brightness_ok = 1.0 - abs(mean_brightness - 128) / 128  # 1.0 at mid-grey
        dynamic_ok = min(1.0, std_brightness / 55)              # saturates at std ≥ 55

        exposure = 1.0 + 4.0 * (
            clipping_ok * 0.4 + brightness_ok * 0.3 + dynamic_ok * 0.3
        )
        exposure = max(1.0, min(5.0, exposure))

        # Noise — mean pixel variation in flat (low-edge) regions
        blurred_arr = np.array(
            gray.filter(ImageFilter.GaussianBlur(radius=1)), dtype=float
        )
        diff = np.abs(gray_arr - blurred_arr)
        flat_mask = edges_arr < (edges_arr.mean() * 0.4)
        noise_level = float(diff[flat_mask].mean()) if flat_mask.sum() > 200 else 0.0
        noise = 1.0 + 4.0 * max(0.0, 1.0 - noise_level / _NOISE_FLAT_HIGH)
        noise = max(1.0, min(5.0, noise))

        combined = sharp * 0.5 + exposure * 0.3 + noise * 0.2
        return max(1, min(5, round(combined)))

    except Exception:
        return 0


def _load_clip_engine(reference_path: Path) -> tuple:
    """Load the CLIP model and reference embeddings for stock-likeness scoring.

    Args:
        reference_path: Path to a .npy embeddings file produced by build_reference.py.

    Returns:
        Tuple of (model, preprocess, reference_embeddings_ndarray, device_string).

    Raises:
        SystemExit: Code 1 if CLIP dependencies are missing or the file cannot be loaded.
    """
    try:
        import numpy as np
        import open_clip
        import torch
    except ImportError as exc:
        err_console.print(f"[red bold]Error:[/] CLIP dependencies not installed: {exc}")
        err_console.print("Fix: [cyan]pip install open_clip_torch numpy Pillow[/]")
        sys.exit(1)

    if not reference_path.exists():
        err_console.print(f"[red bold]Error:[/] CLIP reference file not found: {reference_path}")
        err_console.print("Fix: [cyan]python build_reference.py --download 200[/]")
        sys.exit(1)

    try:
        ref_embs = np.load(str(reference_path))
    except Exception as exc:
        err_console.print(f"[red bold]Error:[/] cannot load CLIP reference embeddings: {exc}")
        sys.exit(1)

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    with console.status(f"Loading CLIP model [dim]({_CLIP_MODEL})[/]..."):
        model, _, preprocess = open_clip.create_model_and_transforms(
            _CLIP_MODEL, pretrained=_CLIP_PRETRAINED
        )
        model = model.to(device)
        model.eval()
    console.print(f"[green]✓[/] CLIP model loaded — [dim]{len(ref_embs)} reference embeddings from {reference_path}[/]")
    return model, preprocess, ref_embs, device


def _compute_clip_score(image_path: Path, clip_engine: tuple) -> float:
    """Score an image against the CLIP reference embedding set.

    Computes the mean cosine similarity of the image embedding against the top-K
    reference embeddings, then maps the result to a 1–5 scale.

    Args:
        image_path: Path to a JPEG or PNG image (HEIC already converted by caller).
        clip_engine: Tuple from _load_clip_engine().

    Returns:
        Stock-likeness score 1.0–5.0, or 0.0 if embedding fails.
    """
    try:
        import numpy as np
        import torch
        from PIL import Image as PILImage

        model, preprocess, ref_embs, device = clip_engine
        img = PILImage.open(image_path).convert("RGB")
        tensor = preprocess(img).unsqueeze(0).to(device)
        with torch.no_grad():
            emb = model.encode_image(tensor)
            emb = emb / emb.norm(dim=-1, keepdim=True)
        emb_np = emb.cpu().float().numpy()  # shape [1, D]

        sims = (ref_embs @ emb_np.T).flatten()  # cosine sim (both L2-normalised)
        top_k = min(_TOP_K, len(sims))
        mean_sim = float(np.sort(sims)[-top_k:].mean())

        score = 1.0 + 4.0 * (mean_sim - _SIM_LOW) / (_SIM_HIGH - _SIM_LOW)
        return round(max(1.0, min(5.0, score)), 1)
    except Exception:
        return 0.0


# Formats that Ollama vision models accept natively
_OLLAMA_NATIVE_FORMATS = {".jpg", ".jpeg", ".png"}


def _to_jpeg_if_needed(path: Path) -> tuple[Path, bool]:
    """Convert an image to a temporary JPEG if Ollama cannot read its format.

    Ollama vision models only handle JPEG and PNG. HEIC, HEIF, TIFF, RAW, and
    other formats must be converted first. Uses macOS sips (built-in), so no
    extra dependency is required.

    Args:
        path: Path to the source image.

    Returns:
        Tuple of (path_to_use, is_temporary). When is_temporary is True the
        caller is responsible for deleting the file after use.

    Raises:
        RuntimeError: If sips conversion fails.
    """
    if path.suffix.lower() in _OLLAMA_NATIVE_FORMATS:
        return path, False

    tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
    tmp.close()
    tmp_path = Path(tmp.name)

    result = subprocess.run(
        ["sips", "-s", "format", "jpeg", str(path), "--out", str(tmp_path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        tmp_path.unlink(missing_ok=True)
        raise RuntimeError(f"sips conversion failed: {result.stderr.strip()}")

    return tmp_path, True


def analyse_photo(
    photo: osxphotos.PhotoInfo,
    model: str,
    clip_engine: tuple | None = None,
) -> PhotoAnalysis:
    """Analyse a single photo using the Ollama vision model and optional CLIP scoring.

    Args:
        photo: osxphotos PhotoInfo object representing the photo.
        model: Ollama model name to use for inference.
        clip_engine: Optional tuple from _load_clip_engine(). When provided,
            clip_score is computed and used instead of commercial_score for
            recommendation thresholds and overall_score.

    Returns:
        Populated PhotoAnalysis dataclass. On failure the error field is set
        and scores default to 0.
    """
    path = Path(photo.path)
    result = PhotoAnalysis(
        filename=path.name,
        original_filename=photo.original_filename or path.name,
        uuid=photo.uuid,
        date_taken=photo.date.strftime("%Y-%m-%d") if photo.date else "",
        original_path=str(path),
    )

    try:
        image_path, is_temp = _to_jpeg_if_needed(path)
        try:
            response = ollama.chat(
                model=model,
                messages=[
                    {
                        "role": "user",
                        "content": _ANALYSIS_PROMPT,
                        "images": [str(image_path)],
                    }
                ],
            )
            # Both metrics require the image file — compute before the finally cleanup
            computed_tech = _compute_technical_score(image_path)
            if clip_engine is not None:
                result.clip_score = _compute_clip_score(image_path, clip_engine)
        finally:
            if is_temp:
                image_path.unlink(missing_ok=True)

        parsed = _extract_json(response.message.content)
        if parsed:
            # technical_score comes from image analysis, not the model
            result.technical_score = computed_tech
            result.commercial_score = max(1, min(5, int(parsed.get("commercial_score", 0))))
            result.subject = str(parsed.get("subject", ""))
            result.keywords = [str(k) for k in parsed.get("keywords", [])]
            rec = str(parsed.get("recommendation", "skip")).lower()
            result.recommendation = rec if rec in ("submit", "maybe", "skip") else "skip"
            result.reason = str(parsed.get("reason", ""))

            # When CLIP is available, use clip_score as the commercial signal for thresholds.
            # The model's commercial_score is still stored but CLIP is more reliable.
            commercial = (
                result.clip_score
                if clip_engine is not None and result.clip_score >= _CLIP_MIN_SIGNAL
                else float(result.commercial_score)
            )
            # Enforce minimum score thresholds regardless of model recommendation.
            # Two separate ifs so a submit→maybe demotion is immediately re-checked
            # against the maybe floor (elif would skip the second check).
            if result.recommendation == "submit" and (
                result.technical_score < 4 or commercial < 4
            ):
                result.recommendation = "maybe"
            if result.recommendation == "maybe" and (
                result.technical_score < 3 or commercial < 3
            ):
                result.recommendation = "skip"
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
            "original_filename": a.original_filename,
            "uuid": a.uuid,
            "date_taken": a.date_taken,
            "original_path": a.original_path,
            "overall_score": a.overall_score,
            "technical_score": a.technical_score,
            "commercial_score": a.commercial_score,
            "clip_score": a.clip_score,
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
        "clip_score",
        "subject",
        "keywords",
        "reason",
        "original_filename",
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
                    "clip_score": a.clip_score,
                    "subject": a.subject,
                    "keywords": "; ".join(a.keywords),
                    "reason": a.reason,
                    "original_filename": a.original_filename,
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
    clip_used = any(a.clip_score > 0.0 for a in analyses)

    scoring_note = "CLIP stock-likeness scoring" if clip_used else "vision model scoring"
    lines: list[str] = [
        "# Photo Scout Report",
        "",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}  ",
        f"Analysed: {len(analyses)} | "
        f"Submit: {len(submit)} | "
        f"Maybe: {len(maybe)} | "
        f"Skip: {len(analyses) - len(submit) - len(maybe)} | "
        f"Scoring: {scoring_note}",
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
            display_name = a.original_filename or a.filename
            lines.append(f"| {a.overall_score} | {display_name} | {a.subject} | {kw} | {a.reason} |")
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
        choices=["json", "csv"],
        default="json",
        dest="output_format",
        help="Primary output format (default: json)",
    )
    parser.add_argument(
        "--markdown",
        action="store_true",
        help="Also write a human-readable Markdown report alongside the primary output",
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
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-analyse all photos, ignoring any existing report",
    )
    parser.add_argument(
        "--clip-reference",
        metavar="FILE",
        type=Path,
        help=(
            "Path to CLIP reference embeddings (.npy) from build_reference.py. "
            "When provided, clip_score replaces the model's commercial_score for "
            "recommendations, giving a data-driven stock-likeness signal."
        ),
    )
    args = parser.parse_args()

    since: datetime | None = None
    if args.since:
        try:
            since = datetime.strptime(args.since, "%Y-%m-%d")
        except ValueError:
            err_console.print(f"[red bold]Error:[/] invalid date '{args.since}' — use YYYY-MM-DD format.")
            sys.exit(1)

    output_path: Path = args.output
    expected_suffix = ".json" if args.output_format == "json" else ".csv"
    if output_path.suffix != expected_suffix:
        output_path = output_path.with_suffix(expected_suffix)

    clip_reference = args.clip_reference
    if clip_reference is None:
        default_ref = Path("clip-reference-embeddings.npy")
        if default_ref.exists():
            clip_reference = default_ref

    clip_engine = None
    if clip_reference:
        clip_engine = _load_clip_engine(clip_reference)

    with console.status(f"Checking Ollama [dim](model: {args.model})[/]..."):
        check_ollama(args.model)
    console.print(f"[green]✓[/] Ollama ready [dim]({args.model})[/]")

    with console.status("Opening Photos library..."):
        photos, skipped_cloud = load_photos(args.library, args.album, since, args.limit)
    console.print(f"[green]✓[/] Photos library opened")

    if skipped_cloud:
        console.print(f"  [yellow]⚠[/]  {skipped_cloud} iCloud-only photo(s) skipped (not downloaded locally).")

    # Load prior results for incremental runs (skipped when --force is set)
    prior_results = {} if args.force else _load_prior_results(output_path)
    already_analysed = set(prior_results.keys())
    photos_to_run = [p for p in photos if str(Path(p.path)) not in already_analysed]

    if prior_results and not args.force:
        console.print(
            f"  [dim]Resuming: {len(prior_results)} already in report, "
            f"{len(photos_to_run)} unanalysed.[/]"
        )

    # Apply --limit to unanalysed photos so it always means "analyse N new photos"
    if args.limit:
        photos_to_run = photos_to_run[: args.limit]

    if not photos_to_run:
        console.print("[dim]Nothing new to analyse. Use --force to re-analyse everything.[/]")
        sys.exit(0)

    console.rule(f"[bold]Analysing {len(photos_to_run)} photo(s)[/]")

    _REC_COLOR = {"submit": "green", "maybe": "yellow", "skip": "dim"}
    n = len(photos_to_run)
    pad = len(str(n))

    new_analyses: list[PhotoAnalysis] = []
    for i, photo in enumerate(photos_to_run, 1):
        name = photo.original_filename or Path(photo.path).name
        label = f"[dim][{i:>{pad}}/{n}][/] {name}"
        with console.status(f"{label}  [dim]analysing...[/]"):
            result = analyse_photo(photo, args.model, clip_engine)
        if result.error:
            console.print(f"{label}  [red]ERROR:[/] {result.error}")
        else:
            pct = round(result.overall_score / 5 * 100)
            if result.clip_score >= _CLIP_MIN_SIGNAL:
                clip_info = f"  clip:[cyan]{result.clip_score}[/]"
            elif result.clip_score > 0.0:
                clip_info = f"  clip:[dim]{result.clip_score}[/]"  # below signal floor, falling back to comm
            else:
                clip_info = ""
            rec_color = _REC_COLOR.get(result.recommendation, "white")
            console.print(
                f"{label}  [{rec_color}]{result.recommendation}[/]"
                f"  tech:{result.technical_score}  comm:{result.commercial_score}"
                f"{clip_info}  [dim]{pct}%[/]"
            )
        new_analyses.append(result)

    prior_analyses = [_analysis_from_dict(v) for v in prior_results.values()]
    analyses = prior_analyses + new_analyses
    analyses.sort(key=_sort_key)

    if args.output_format == "json":
        write_json(analyses, output_path)
    else:
        write_csv(analyses, output_path)

    if args.markdown:
        md_path = output_path.with_suffix(".md")
        write_markdown(analyses, md_path)

    submit_count = sum(1 for a in analyses if a.recommendation == "submit")
    maybe_count = sum(1 for a in analyses if a.recommendation == "maybe")
    error_count = sum(1 for a in analyses if a.error)
    skip_count = len(analyses) - submit_count - maybe_count

    console.rule()
    console.print(
        f"[bold]Done.[/] {len(new_analyses)} new photo(s) analysed [dim]({len(analyses)} total in report)[/]"
    )
    console.print(
        f"  [green]Submit: {submit_count}[/]  "
        f"[yellow]Maybe: {maybe_count}[/]  "
        f"[dim]Skip: {skip_count}[/]"
    )
    if error_count:
        console.print(f"  [red]Errors: {error_count}[/] (see report for details)")
    console.print(f"\n[dim]Report:[/]   {output_path.resolve()}")
    if args.markdown:
        console.print(f"[dim]Markdown:[/] {output_path.with_suffix('.md').resolve()}")


if __name__ == "__main__":
    main()
