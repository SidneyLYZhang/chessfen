# Run chess analysis in the browser with direct BYOK LLM access

Status: accepted — specified in #20.

ChessFEN runs Stockfish WASM exclusively in the Visitor's browser, using a pinned, self-hosted `nmrugg/stockfish.js` distribution with adaptive multithreaded and single-threaded builds. Only the Analysis document enables the cross-origin isolation needed by multithreading. python-chess remains available through same-origin page-internal routes, but no public Analysis API or server-side engine is introduced. LLM Explanations use a Visitor-supplied credential sent directly from the browser to the selected Provider; ChessFEN does not proxy LLM requests or take custody of the credential, which is encrypted in local browser storage.

## Considered Options

- **Run Stockfish and LLM requests on the server**: rejected because it makes the operator responsible for engine capacity and third-party credential custody, and conflicts with the local-analysis privacy goal.
- **Require multithreaded WASM everywhere**: rejected because secure-context, cross-origin-isolation, SharedArrayBuffer, browser, and embedded-resource requirements would exclude otherwise capable Visitors.
- **Use only single-threaded WASM**: rejected because it unnecessarily sacrifices analysis throughput for Visitors whose browsers can safely run the multithreaded build.
- **Proxy direct-provider failures through ChessFEN**: rejected because silently changing the credential trust boundary is worse than reporting a CORS limitation. DeepSeek and Kimi therefore remain marked as browser-direct-unverified until real-origin checks pass.

## Consequences

- Analysis is a deliberate, scoped exception to ADR 0004's no-JavaScript main-flow baseline. The existing Jinja shell, hand-written design system, localization, and other pages' behavior remain unchanged; Analysis uses JavaScript for WASM, Workers, interactive Candidate Lines, local credential protection, and resumable workspaces.
- Full credential and multithreaded functionality requires HTTPS or localhost. Plain HTTP is limited to engine-only Analysis with credential features disabled. Correct WASM MIME types, analysis-only COOP/COEP headers, a compatible restrictive CSP, and real-browser tests are required.
- The lite engine resources are the default; full resources require an explicit Visitor download. Upstream license, provenance, and corresponding-source information must accompany the pinned distribution.
- OpenAI-compatible transport does not imply a common Reasoning contract. OpenAI, OpenRouter, DeepSeek, Kimi, and Custom require explicit Reasoning dialect handling and model-specific confirmation rather than silent omission or downgrade.
- ChessFEN stores neither an analysis history nor Provider credentials on the server. One resumable Analysis Workspace and the encrypted credential are local to the Visitor's browser, with the security limitations of browser storage documented in the parent spec.
