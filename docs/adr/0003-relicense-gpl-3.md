# Relicense ChessFEN as GPL-3.0-or-later

Status: accepted

The project was specified as MIT, but its runtime imports python-chess (the `chess` distribution), which is licensed GPL-3.0-or-later. Distributing the Docker image links the two into one combined work, so an MIT grant for the project would have been misleading. ChessFEN is therefore licensed GPL-3.0-or-later, with third-party components recorded in NOTICE: python-chess (GPL-3.0+), the 2d-chess-ocr weights (MIT as published), the CBurnett piece set (CC BY-SA 3.0), and resvg_py/resvg (MIT / MPL-2.0).

## Considered Options

- **Keep MIT and disclose the GPL dependency**: the combined distribution is still governed by the GPL, so an MIT headline would mislead. Rejected.
- **Replace python-chess**: FEN validation, SVG rendering, and PGN/SAN parsing would need reimplementation or new permissive dependencies, with real regression risk. Rejected for now.

## Consequences

- LICENSE contains the GPL-3.0 text; README, the site footer, and the about page state GPL-3.0-or-later.
- ADR-0001 and ADR-0002 remain valid in substance; their MIT references predate this ADR.