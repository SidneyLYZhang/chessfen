#!/usr/bin/env python3
"""Regenerate favicon and icon deliverables from the approved brand SVGs.

Usage:
    uv run python scripts/generate_brand_icons.py

The SVG masters live in the packaged static brand directory; this script
rasterizes them with resvg-py and writes the ICO/PNG companions. The outputs
are committed, so the script only needs to run when a master changes.
"""

from __future__ import annotations

import io
from pathlib import Path

import resvg_py
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
BRAND = ROOT / "src" / "chessfen" / "static" / "brand"
PANEL = "#F3F0E8"


def render(svg_name: str, size: int, background: str | None = None) -> Image.Image:
    png = bytes(
        resvg_py.svg_to_bytes(
            svg_path=str(BRAND / svg_name),
            width=size,
            height=size,
        )
    )
    image = Image.open(io.BytesIO(png)).convert("RGBA")
    if background is None:
        return image
    base = Image.new("RGBA", image.size, background)
    base.alpha_composite(image)
    return base


def save_png(
    svg_name: str, size: int, filename: str, background: str | None = None
) -> None:
    render(svg_name, size, background).save(
        BRAND / filename, format="PNG", optimize=True
    )


def save_ico(svg_name: str, filename: str) -> None:
    render(svg_name, 256).save(
        BRAND / filename,
        format="ICO",
        sizes=[(16, 16), (32, 32), (48, 48), (64, 64)],
    )


def main() -> None:
    save_ico("favicon.svg", "favicon.ico")
    save_ico("mark-reverse.svg", "favicon-dark.ico")
    save_png("mark.svg", 180, "apple-touch-icon.png", background=PANEL)
    save_png("mark.svg", 192, "icon-192.png")
    save_png("mark.svg", 512, "icon-512.png")


if __name__ == "__main__":
    main()