#!/usr/bin/env python3
"""build_reference.py - Build a CLIP reference embedding set for stock photo scoring.

Module:
    build_reference

Description:
    Downloads a curated set of featured photos from Wikimedia Commons (no API key
    required) and/or imports photos from a local directory, computes CLIP embeddings
    for each, and saves the result to a NumPy .npy file.

    The embeddings are used by photo_scout.py --clip-reference to score new photos
    by cosine similarity to accepted stock-quality images — a data-driven signal for
    commercial appeal that does not depend on the vision model's judgment.

    Wikimedia Commons Featured Pictures are hand-curated by Wikipedia editors for
    technical quality and encyclopedic value: a useful proxy for stock photo standards,
    freely downloadable with no authentication.

Author:
    Alister Lewis-Bowen <alister@lewis-bowen.org>

Version:
    0.1.0

Date:
    2026-05-10

License:
    MIT

Usage:
    python build_reference.py [--source DIR] [--download N] [--output FILE]

Dependencies:
    open_clip_torch, torch, Pillow, numpy, requests
    Install: pip install open_clip_torch numpy Pillow requests

Exit Codes:
    0 - Success
    1 - Missing dependency or invalid argument
    2 - No images found or processed
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# CLIP model — must match _CLIP_MODEL / _CLIP_PRETRAINED in photo_scout.py
_CLIP_MODEL = "ViT-B-32"
_CLIP_PRETRAINED = "openai"

_WIKIMEDIA_API = "https://commons.wikimedia.org/w/api.php"
_MAX_IMAGE_BYTES = 8 * 1024 * 1024  # skip files over 8 MB


def _require_deps() -> tuple:
    """Import CLIP dependencies, exiting with a helpful message if absent.

    Returns:
        Tuple of (numpy, open_clip, torch, PIL.Image, requests) modules.

    Raises:
        SystemExit: Code 1 if any dependency is missing.
    """
    try:
        import numpy as np
        import open_clip
        import requests
        import torch
        from PIL import Image

        return np, open_clip, torch, Image, requests
    except ImportError as exc:
        print(f"Error: CLIP dependencies not installed: {exc}", file=sys.stderr)
        print(
            "Fix: pip install open_clip_torch numpy Pillow requests",
            file=sys.stderr,
        )
        sys.exit(1)


def _fetch_wikimedia_titles(n_wanted: int, session) -> list[str]:
    """Collect file titles from Wikimedia Commons Featured Pictures category.

    Args:
        n_wanted: Minimum number of titles to collect (fetches more to allow filtering).
        session: requests.Session to use.

    Returns:
        List of Wikimedia file title strings (e.g. 'File:Foo.jpg').
    """
    titles: list[str] = []
    params: dict = {
        "action": "query",
        "list": "categorymembers",
        "cmtitle": "Category:Featured_pictures_on_Wikimedia_Commons",
        "cmlimit": "500",
        "cmtype": "file",
        "format": "json",
    }
    while len(titles) < n_wanted:
        resp = session.get(_WIKIMEDIA_API, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        members = data.get("query", {}).get("categorymembers", [])
        titles.extend(m["title"] for m in members)
        cont = data.get("continue", {}).get("cmcontinue")
        if not cont:
            break
        params["cmcontinue"] = cont
    return titles


def _fetch_image_urls(titles: list[str], session) -> list[str]:
    """Resolve Wikimedia file titles to direct download URLs.

    Filters to JPEG/PNG files under the size limit.

    Args:
        titles: Wikimedia file title strings.
        session: requests.Session to use.

    Returns:
        List of direct image download URLs.
    """
    urls: list[str] = []
    for i in range(0, len(titles), 50):
        batch = titles[i : i + 50]
        params = {
            "action": "query",
            "titles": "|".join(batch),
            "prop": "imageinfo",
            "iiprop": "url|size|mime|mediatype",
            "format": "json",
        }
        resp = session.get(_WIKIMEDIA_API, params=params, timeout=30)
        resp.raise_for_status()
        pages = resp.json().get("query", {}).get("pages", {}).values()
        for page in pages:
            info_list = page.get("imageinfo", [])
            if not info_list:
                continue
            info = info_list[0]
            if info.get("mime") not in ("image/jpeg", "image/png"):
                continue
            if info.get("size", 0) > _MAX_IMAGE_BYTES:
                continue
            # skip SVG/vector disguised as BITMAP
            if info.get("mediatype") not in ("BITMAP", None, ""):
                continue
            urls.append(info["url"])
    return urls


def _download_photos(urls: list[str], dest_dir: Path, session, n: int) -> list[Path]:
    """Download image files to dest_dir, up to n total.

    Skips files already present in dest_dir (idempotent).

    Args:
        urls: Direct image download URLs.
        dest_dir: Directory to save downloaded files.
        session: requests.Session to use.
        n: Maximum number of files to download.

    Returns:
        List of paths to downloaded files (including pre-existing ones).
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for url in urls:
        if len(paths) >= n:
            break
        filename = url.split("/")[-1].split("?")[0]
        dest = dest_dir / filename
        if dest.exists():
            paths.append(dest)
            continue
        try:
            resp = session.get(url, timeout=60, stream=True)
            resp.raise_for_status()
            with dest.open("wb") as fh:
                for chunk in resp.iter_content(chunk_size=65536):
                    fh.write(chunk)
            paths.append(dest)
            print(f"  Downloaded: {filename}")
        except Exception as exc:
            print(f"  Warning: failed to download {filename}: {exc}")
    return paths


def _load_clip_model(open_clip, torch, device: str) -> tuple:
    """Load the CLIP model and image preprocessing pipeline.

    Args:
        open_clip: Imported open_clip module.
        torch: Imported torch module.
        device: Torch device string ('cpu', 'mps', etc.).

    Returns:
        Tuple of (model, preprocess).
    """
    print(f"Loading CLIP model {_CLIP_MODEL} / {_CLIP_PRETRAINED}...")
    model, _, preprocess = open_clip.create_model_and_transforms(
        _CLIP_MODEL, pretrained=_CLIP_PRETRAINED
    )
    model = model.to(device)
    model.eval()
    return model, preprocess


def _embed_images(
    paths: list[Path],
    model,
    preprocess,
    Image,
    torch,
    np,
    device: str,
) -> tuple:
    """Compute L2-normalised CLIP embeddings for a list of image files.

    Args:
        paths: Image file paths.
        model: Loaded CLIP model.
        preprocess: CLIP image preprocessing pipeline.
        Image: PIL.Image module.
        torch: torch module.
        np: numpy module.
        device: Torch device string.

    Returns:
        Tuple of (embeddings ndarray [N, D], list of source path strings).
    """
    embeddings: list = []
    sources: list[str] = []
    for i, path in enumerate(paths, 1):
        try:
            img = Image.open(path).convert("RGB")
            tensor = preprocess(img).unsqueeze(0).to(device)
            with torch.no_grad():
                emb = model.encode_image(tensor)
                emb = emb / emb.norm(dim=-1, keepdim=True)
            embeddings.append(emb.cpu().float().numpy())
            sources.append(str(path))
            print(f"  [{i}/{len(paths)}] {path.name}")
        except Exception as exc:
            print(f"  Warning: skipping {path.name}: {exc}")
    if not embeddings:
        return np.empty((0, 512), dtype=np.float32), []
    return np.vstack(embeddings).astype(np.float32), sources


def main() -> None:
    """Entry point: parse args, collect images, compute embeddings, save."""
    parser = argparse.ArgumentParser(
        description="Build a CLIP reference embedding set for photo-scout.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  # Download 200 Wikimedia Commons featured photos and embed them\n"
            "  python build_reference.py --download 200\n\n"
            "  # Embed photos from a local directory of good stock samples\n"
            "  python build_reference.py --source ~/good-stock-photos\n\n"
            "  # Combine local and downloaded photos\n"
            "  python build_reference.py --source ~/my-photos --download 100\n\n"
            "  # Use the resulting embeddings file when running photo_scout.py:\n"
            "  python photo_scout.py --clip-reference clip-reference-embeddings.npy\n"
        ),
    )
    parser.add_argument(
        "--source",
        metavar="DIR",
        type=Path,
        help="Local directory of reference photos to embed",
    )
    parser.add_argument(
        "--download",
        metavar="N",
        type=int,
        default=0,
        help="Download N photos from Wikimedia Commons featured pictures (default: 0)",
    )
    parser.add_argument(
        "--output",
        metavar="FILE",
        type=Path,
        default=Path("clip-reference-embeddings.npy"),
        help="Output .npy embeddings file (default: clip-reference-embeddings.npy)",
    )
    parser.add_argument(
        "--download-dir",
        metavar="DIR",
        type=Path,
        default=Path("clip-reference"),
        help="Directory to cache downloaded photos (default: clip-reference/)",
    )
    args = parser.parse_args()

    if not args.source and not args.download:
        parser.error("Provide --source, --download, or both.")

    np, open_clip, torch, Image, requests = _require_deps()

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    print(f"Device: {device}")

    model, preprocess = _load_clip_model(open_clip, torch, device)

    all_paths: list[Path] = []

    if args.source:
        if not args.source.is_dir():
            print(f"Error: --source '{args.source}' is not a directory.", file=sys.stderr)
            sys.exit(1)
        local_paths = [
            p
            for p in sorted(args.source.iterdir())
            if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".heic", ".tiff", ".tif")
        ]
        print(f"Found {len(local_paths)} image(s) in {args.source}")
        all_paths.extend(local_paths)

    if args.download > 0:
        print(f"Fetching {args.download} photos from Wikimedia Commons featured pictures...")
        session = requests.Session()
        session.headers["User-Agent"] = (
            "photo-scout/1.0 (https://github.com/ali5ter/photo-scout)"
        )
        # Fetch 4× titles to account for filtering losses (non-JPEG, oversized, etc.)
        titles = _fetch_wikimedia_titles(args.download * 4, session)
        print(f"  Found {len(titles)} featured file titles — resolving URLs...")
        urls = _fetch_image_urls(titles, session)
        print(f"  {len(urls)} JPEG/PNG files under {_MAX_IMAGE_BYTES // (1024*1024)} MB")
        downloaded = _download_photos(urls, args.download_dir, session, args.download)
        print(f"  Downloaded/cached: {len(downloaded)} photo(s)")
        all_paths.extend(downloaded)

    if not all_paths:
        print("Error: no images to process.", file=sys.stderr)
        sys.exit(2)

    print(f"\nComputing embeddings for {len(all_paths)} image(s)...")
    embeddings, sources = _embed_images(
        all_paths, model, preprocess, Image, torch, np, device
    )

    if len(embeddings) == 0:
        print("Error: no embeddings computed — all images failed.", file=sys.stderr)
        sys.exit(2)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    np.save(str(args.output), embeddings)

    meta_path = args.output.with_suffix(".json")
    meta_path.write_text(
        json.dumps(
            {
                "model": _CLIP_MODEL,
                "pretrained": _CLIP_PRETRAINED,
                "count": len(embeddings),
                "sources": sources,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"\nDone. {len(embeddings)} embeddings → {args.output.resolve()}")
    print(f"Metadata: {meta_path.resolve()}")
    print(
        "\nUse with photo_scout.py:\n"
        f"  python photo_scout.py --clip-reference {args.output}"
    )


if __name__ == "__main__":
    main()
