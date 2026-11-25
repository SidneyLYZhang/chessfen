# chessfen

English | [中文](README.zh-CN.md)

Python tool (API + web app) for working with chess encodings — PGN, FEN — and board position images.

## Features

- **Board recognition**: upload a 2D board image and get its Placement, with per-square confidences.
- **PGN navigation**: choose a game from a multi-game PGN and export the Full FEN after any Ply.
- **FEN rendering**: turn any FEN into a PNG or SVG board image.
- **Web UI and API**: API keys are issued from an admin page; the interface is available in Chinese, English, and German.

## What's new in v1.1.0

- Every page is rebuilt on one brand-aligned design system: warm paper and ochre surfaces, structural ink, self-hosted Space Grotesk and IBM Plex Mono, a new mark, and matching favicons.
- PGN navigation adds a board preview and a clickable Mainline move grid while keeping the precise "after White's/Black's Nth move" controls; FEN rendering gains brand board colors, a Full FEN field summary, and server-rendered example links.
- Recognition shows the source image beside a reconstructed board, copyable Placement and Full FEN, and a 64-square confidence breakdown; the guides, About, admin login and key management, and a localized 404 are rebuilt on the same system.
- All main flows still work without JavaScript; the interface remains Chinese, English, and German.
- Container namespace references are corrected to `sidneylyzhang` throughout.

## Quick start (Docker)

Each release publishes prebuilt images to `ghcr.io/sidneylyzhang/chessfen` and to Docker Hub as `sidneylyzhang/chessfen`, with the pinned recognition model baked in. Tags include `latest` and version tags such as `v1.1.0`.

```bash
docker run -d --name chessfen \
  -p 127.0.0.1:8000:8000 \
  -e ADMIN_PASSWORD='replace-with-a-strong-password' \
  -e SECRET_KEY='replace-with-a-long-random-secret' \
  -v chessfen-data:/data \
  ghcr.io/sidneylyzhang/chessfen:latest
```

Open <http://localhost:8000> and create API keys on the `/admin` page.

Or run it with the compose file:

```bash
cp .env.example .env   # edit the passwords
docker compose up -d
```

`docker-compose.yml` targets the published image and binds the published port to `127.0.0.1`. To build from source instead:

```bash
docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --build
```

While the repository and its GHCR package are private, authenticate before pulling: `echo "$GITHUB_TOKEN" | docker login ghcr.io -u <github-user> --password-stdin`.

## Development

Managed with [uv](https://docs.astral.sh/uv/). Requires Python 3.13+.

```bash
uv sync                                  # install the project and its dependencies
uv run playwright install chromium       # install the real-WASM browser test runtime
uv run alembic upgrade head              # apply database migrations (SQLite by default)
uv run chessfen                          # serve on http://127.0.0.1:8000
uv run pytest                            # run the test suite
```

## Configuration

- `CHESSFEN_IMAGE` — image used by `docker-compose.yml` (default `ghcr.io/sidneylyzhang/chessfen:latest`).
- `DATABASE_URL` — defaults to `sqlite:////data/chessfen.db` inside the container; set a PostgreSQL URL (for example `postgresql+psycopg://user:pass@host/db`) to switch.
- `ADMIN_PASSWORD` — enables the admin page; when unset, admin login is disabled.
- `SECRET_KEY` — signs the admin session cookie; required when `ADMIN_PASSWORD` is set.
- `FORWARDED_ALLOW_IPS` — which peers may set `X-Forwarded-*` headers (default `127.0.0.1`). Set it to your reverse proxy's address or network so rate limiting sees real client IPs.

### Recognition weights (local development)

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

The container serves plain HTTP on port 8000, and `docker-compose.yml` binds the published port to `127.0.0.1` by default. Terminate TLS at your own reverse proxy, forward `Host`, `X-Forwarded-For`, and `X-Forwarded-Proto`, and set `FORWARDED_ALLOW_IPS` to the proxy address so the app trusts those headers only from it.

## API

The API is gated by an API key sent as `X-API-Key: cf_...`; keys are issued from the admin page. Endpoints: `POST /api/v1/render`, `POST /api/v1/pgn`, `POST /api/v1/recognize`.

## Releases

Push an annotated version tag to publish a release:

```bash
git tag -a v1.1.0 -m "ChessFEN 1.1.0"
git push origin v1.1.0
```

The `Release` workflow gates on the shared `Test` workflow (which also runs on pushes to `main` and pull requests): unit tests, real Chromium/WASM Position Analysis tests, mypy, and a Docker image smoke test that boots the container, serves the pinned browser engine assets, issues an API key, and performs a real recognition through the baked model. CI downloads the pinned model weights and Chromium so the real-engine tests run instead of being skipped.

After the tests pass, two independent jobs run:

1. **Publish**: builds and pushes the image to GHCR (`ghcr.io/sidneylyzhang/chessfen`) and Docker Hub (`sidneylyzhang/chessfen`), tagged with the release tag, plus `latest` for stable tags.
2. **GitHub release**: creates a release whose notes are the annotated tag message; lightweight tags fall back to `Release <tag>`. Tags containing a hyphen are marked as prereleases and do not move `latest`.

The GitHub release is independent of image publication, so a Docker Hub problem never blocks it.

Required repository secrets for Docker Hub: `DOCKERHUB_USERNAME` (lowercase) and `DOCKERHUB_TOKEN`; the publish job fails with a clear message when they are missing. GHCR uses the built-in `GITHUB_TOKEN`; make the GHCR package public if anonymous pulls are desired.

## License

ChessFEN is licensed under GPL-3.0-or-later; see [LICENSE](LICENSE) for the full text and [NOTICE](NOTICE) for third-party components (python-chess GPL-3.0+, 2d-chess-ocr weights MIT, CBurnett pieces CC BY-SA 3.0, resvg_py/resvg MIT/MPL-2.0, Space Grotesk and IBM Plex Mono under the SIL OFL 1.1).

## Agent configuration

Engineering skills read their per-repo configuration from `docs/agents/` (issue tracker, triage labels, domain docs). See `AGENTS.md`.
