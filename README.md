# chessfen

Python tool (API + web app) for working with chess encodings — PGN, FEN — and board position images.

## Development

Managed with [uv](https://docs.astral.sh/uv/). Requires Python 3.13+.

```bash
uv sync          # install the project and its dependencies
uv run chessfen  # run the CLI entry point
```

## Agent configuration

Engineering skills read their per-repo configuration from `docs/agents/` (issue tracker, triage labels, domain docs). See `AGENTS.md`.