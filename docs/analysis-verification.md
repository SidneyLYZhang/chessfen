# Analysis release verification

Date: 2026-11-26
Scope: Analysis workbench, settings, LLM Providers, exports, and regressions.

## Automated gates

- `uv run pytest -q`: 296 passed (final integrated suite).
- `uv run mypy`: src, tests, and scripts; no issues.
- JavaScript syntax checks: `node --check` on all changed ES modules.
- CI installs and exercises Chromium, Firefox, and WebKit Playwright runtimes.
- Existing unit, shell, brand, public API, PGN, Rendering, Recognition, Admin, and
  Docker/static smoke gates remain part of CI.

## Localization

- Critical Analysis workbench, settings, provider, export controls, and downloaded
  Markdown reports are checked in Chinese, English, and German.
- PGN annotations use portable English quality comments plus standard NAGs so
  external chess tools can interpret them consistently.
- Export download tests run in all three UI languages.

## Accessibility and responsive layout

Automated checks cover:

- one level-one heading on the Analysis, settings, and Game pages plus labelled
  critical controls;
- keyboard-visible focus using the shared `:focus-visible` outline;
- `role="status"` and `aria-live="polite"` engine status updates;
- keyboard activation of Game Analysis, text Move quality labels in addition to
  color;
- no horizontal page overflow at a 390×844 mobile viewport in English,
  Chinese, and German;
- critical start/export controls remain reachable at that viewport.

These checks do not replace a formal assistive-technology audit. No screen-reader
release check is claimed here.

## Browser compatibility

| Browser | Position smoke: analyze + export | Game smoke: analyze + export | Download | Status |
| --- | --- | --- | --- | --- |
| Chromium 159.0.8154.6 (Playwright) | Pass | Pass | Pass | Pass |
| Firefox 159.0 (Playwright) | Pass | Pass | Pass | Pass |
| WebKit 27.2 (Playwright) | Pass | Pass | Pass | Pass |
| Current stable Chrome | Not separately automated | Not separately automated | Not separately automated | Playwright Chromium is the proxy |
| Mobile Safari | Not available | Not available | Not available | Explicit external blocker |

## Provider compatibility

| Provider | Sanitized/browser tests | Real low-cost browser check | Claim |
| --- | --- | --- | --- |
| OpenAI | Pass | Not run: no real key in this environment/CI | Normal preset, fixture-verified only |
| OpenRouter | Pass | Not run: no real key | Normal preset, fixture-verified only |
| DeepSeek | Pass | Not run: no real key | Browser-direct unverified |
| Kimi China | Pass | Not run: no real key | Browser-direct unverified |
| Kimi International | Pass | Not run: no real key | Browser-direct unverified |
| Custom Provider | Pass against a real local HTTPS cross-origin fixture | Local only | Unknown external endpoints remain Visitor responsibility |

No real credential enters CI. No ChessFEN server LLM proxy exists. DeepSeek and
Kimi retain the in-product unverified label.

## Privacy and credential boundary

- Browser network tests confirm Provider requests go only to the confirmed
  HTTPS Base URL and not to ChessFEN same-origin routes.
- Ciphertext-at-rest inspection confirms plaintext API keys are absent from
  localStorage, IndexedDB, Workspace data, console output, and downloads.
- Raw reasoning is discarded and is absent from pages, Workspace records, PGN,
  and Markdown exports.
- Changing a Provider, Kimi region, or Custom endpoint invalidates the previous
  consent and credential binding.

## Performance

Required accepted scenario: ordinary four-core desktop, current stable Chrome,
multithreaded lite, Standard preset, MultiPV three, 60-Ply Game, under 150
seconds.

- Multithreaded runtime status: **blocked by #34**. The current Analysis document
  runs the single-threaded lite engine; multithreaded SharedArrayBuffer isolation
  is not available, so the required multithreaded result cannot be claimed.
- Single-threaded 60-Ply spot check on 2026-11-26:
  - Browser: Playwright Chromium 159.0.8154.6.
  - Platform: Windows AMD64, 24 logical CPUs; runtime reported
    `crossOriginIsolated: false`.
  - Standard preset, MultiPV three, lite single-threaded.
  - Elapsed analysis time: **105.93 seconds**.
  - This is below 150 seconds but is not the accepted multithreaded scenario and
    not the documented ordinary four-core reference hardware.
- Cancellation measurement from the same run, using an in-page MutationObserver:
  - UI "Stopping…" feedback after the cancel action: **0.6 ms** (target 500 ms).
  - Engine stop to cancelled state: **30.8 ms** (target ordinarily 2 seconds).

Command:

```sh
uv run python scripts/benchmark_analysis.py --plies 60 --timeout 600
```

## Unmet external checks / blockers

1. Real low-cost OpenAI, OpenRouter, DeepSeek, and both Kimi regional checks were
   not performed because no real credentials are available in this environment
   and real keys must not enter CI. DeepSeek/Kimi remain labelled unverified.
2. Multithreaded Stockfish and its 60-Ply benchmark are blocked by #34.
3. Mobile Safari release verification was not performed.
4. A formal screen-reader/assistive-technology audit was not performed; only the
   automated accessibility checks listed above are claimed.
5. A locally installed current stable Chrome release was not separately tested;
   Playwright Chromium is the automated proxy.
6. Docker/static smoke tests were not run locally because Docker is unavailable in
   this environment; the existing CI Docker job remains the release gate.
7. Firefox/WebKit coverage is a primary smoke path, not the full Chromium suite;
   cancellation, Provider dialects, recovery, accessibility, and all locales are
   not repeated per engine. Mobile Safari is the external blocker for that engine
   family.
