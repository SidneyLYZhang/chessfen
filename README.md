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

## Deployment (Docker)

Build and run with Docker Compose. The image bakes in the pinned recognition model at build time (about 82 MB download):

```bash
ADMIN_PASSWORD='replace-with-a-strong-password' \
SECRET_KEY='replace-with-a-long-random-secret' \
docker compose up --build
```

Then open <http://localhost:8000> and create API keys on the `/admin` page.

### Configuration

- `DATABASE_URL` — defaults to `sqlite:////data/chessfen.db` inside the container; set a PostgreSQL URL (for example `postgresql+psycopg://user:pass@host/db`) to switch.
- `ADMIN_PASSWORD` — enables the admin page; when unset, admin login is disabled.
- `SECRET_KEY` — signs the admin session cookie; required when `ADMIN_PASSWORD` is set.
- `FORWARDED_ALLOW_IPS` — which peers may set `X-Forwarded-*` headers (default `127.0.0.1`). Set it to your reverse proxy's address or network so rate limiting sees real client IPs.

### Recognition weights

The Docker image bakes in the pinned model. For local development, download it once:

```bash
mkdir -p models
curl -fL -o models/yolo26m-finetuned.onnx \
  https://huggingface.co/AndrewSpano/2d-chess-ocr/resolve/03d9df9fc14fade1a3579683fd0de215b3864ee1/yolo26m-finetuned.onnx
```

Override the location with `RECOGNITION_MODEL_PATH` (default `models/yolo26m-finetuned.onnx`).

### Data

SQLite data lives in the `chessfen-data` volume mounted at `/data`. When using PostgreSQL, point `DATABASE_URL` at the server; the volume is then unused.

### TLS and reverse proxy

The container serves plain HTTP on port 8000; `docker-compose.yml` binds the published port to `127.0.0.1` by default. Terminate TLS at your own reverse proxy, forward `Host`, `X-Forwarded-For`, and `X-Forwarded-Proto`, and set `FORWARDED_ALLOW_IPS` to the proxy address so the app trusts those headers only from it.

## API

The API is gated by an API key sent as `X-API-Key: cf_...`; keys are issued from the admin page. Endpoints: `POST /api/v1/render`, `POST /api/v1/pgn`, `POST /api/v1/recognize`.

## Releases

Push an annotated version tag to publish a release:

```bash
git tag -a v0.2.0 -m "ChessFEN 0.2.0"
git push origin v0.2.0
```

The `Release` workflow gates on the shared `Test` workflow (which also runs on pushes to `main` and pull requests) and then runs two independent jobs:

1. **Test**: unit tests, mypy, and a Docker image smoke test that boots the container, issues an API key, and performs a real recognition through the baked model. CI downloads the pinned model weights so the real-model tests run instead of being skipped.
2. **Publish**: builds and pushes the image to GHCR (`ghcr.io/sidnelyzhang/chessfen`) and Docker Hub (`<DOCKERHUB_USERNAME>/chessfen`), tagged with the release tag, plus `latest` for stable tags.
3. **GitHub release**: creates a release whose notes are the annotated tag message; lightweight tags fall back to `Release <tag>`. Tags containing a hyphen are marked as prereleases and do not move `latest`.

The GitHub release is independent of image publication, so a Docker Hub problem never blocks it.

Required repository secrets for Docker Hub: `DOCKERHUB_USERNAME` (lowercase) and `DOCKERHUB_TOKEN`; the publish job fails with a clear message when they are missing. GHCR uses the built-in `GITHUB_TOKEN`; make the GHCR package public if anonymous pulls are desired.

## License

ChessFEN is licensed under GPL-3.0-or-later; see [LICENSE](LICENSE) for the full text and [NOTICE](NOTICE) for third-party components (python-chess GPL-3.0+, 2d-chess-ocr weights MIT, CBurnett pieces CC BY-SA 3.0, resvg_py/resvg MIT/MPL-2.0).

## Agent configuration

Engineering skills read their per-repo configuration from `docs/agents/` (issue tracker, triage labels, domain docs). See `AGENTS.md`.