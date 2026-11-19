# chessfen

Python tool (API + web app) for working with chess encodings — PGN, FEN — and board position images.

## Development

Managed with [uv](https://docs.astral.sh/uv/). Requires Python 3.13+.

```bash
uv sync                      # install the project and its dependencies
uv run alembic upgrade head  # apply database migrations (SQLite by default)
uv run chessfen              # serve on http://127.0.0.1:8000
uv run pytest                # run the test suite
```

Deploy with Docker:

```bash
docker compose up --build
```

Set `DATABASE_URL` to point at PostgreSQL instead of the default SQLite file.

## Agent configuration

Engineering skills read their per-repo configuration from `docs/agents/` (issue tracker, triage labels, domain docs). See `AGENTS.md`.