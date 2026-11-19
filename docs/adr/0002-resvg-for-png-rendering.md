# Render PNG with resvg instead of cairosvg

Status: accepted

The MVP spec proposed converting python-chess SVG output to PNG with cairosvg. cairosvg depends on native cairo (libcairo2), which is absent on the Windows development machine and would require a system package in the Docker image. We instead rasterize with **resvg_py** (Rust resvg bindings with self-contained wheels for Windows, Linux, and macOS), verified on the development machine to produce a valid PNG at the requested dimensions from python-chess board SVGs (visual fidelity is not pixel-tested). resvg-py is MIT-licensed and bundles resvg (MPL-2.0); both are compatible with this MIT project. SVG output still comes from python-chess — only the rasterizer changed.

## Consequences

- No system libraries are needed in development or in the Docker image, and the PNG path is testable on any platform.
- The rasterizer handles the SVG elements python-chess emits (paths and rects); coordinate labels are path glyphs, so no fonts are required and richer SVG features are out of scope.
- Relicensing note: the project moved to GPL-3.0-or-later in ADR-0003; resvg_py (MIT) and resvg (MPL-2.0) remain compatible.
