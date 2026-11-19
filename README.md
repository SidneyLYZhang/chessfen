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

Configuration (environment):

- `DATABASE_URL` — defaults to `sqlite:///./data/chessfen.db`; point it at PostgreSQL to switch.
- `ADMIN_PASSWORD` — enables the admin page at `/admin`; when unset, admin login is disabled.
- `SECRET_KEY` — signs the admin session cookie; set a long random value in production.

The API is gated by an API key sent as `X-API-Key: cf_...`; keys are issued from the admin page.

## Agent configuration

Engineering skills read their per-repo configuration from `docs/agents/` (issue tracker, triage labels, domain docs). See `AGENTS.md`.