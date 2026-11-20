from __future__ import annotations

from fastapi.testclient import TestClient

# Guard the misspelling without embedding the wrong spelling in the source
# tree: the string is assembled from its correct prefix and remainder.
_MISSPELLED_NAMESPACE = "sidnel" + "yzhang"

_STATIC_ASSETS = [
    ("/static/brand/mark.svg", "image/svg+xml"),
    ("/static/brand/mark-mono.svg", "image/svg+xml"),
    ("/static/brand/mark-reverse.svg", "image/svg+xml"),
    ("/static/brand/favicon.svg", "image/svg+xml"),
    ("/static/brand/favicon.ico", "image/vnd.microsoft.icon"),
    ("/static/brand/favicon-dark.ico", "image/vnd.microsoft.icon"),
    ("/static/brand/apple-touch-icon.png", "image/png"),
    ("/static/brand/icon-192.png", "image/png"),
    ("/static/brand/icon-512.png", "image/png"),
    (
        "/static/fonts/space-grotesk-latin-variable.woff2",
        "font/woff2",
    ),
    ("/static/fonts/ibm-plex-mono-latin-400.woff2", "font/woff2"),
    ("/static/fonts/ibm-plex-mono-latin-500.woff2", "font/woff2"),
    ("/static/fonts/OFL-SpaceGrotesk.txt", "text/plain"),
    ("/static/fonts/OFL-IBMPlexMono.txt", "text/plain"),
    ("/static/css/legacy.css", "text/css"),
    ("/static/css/app.css", "text/css"),
]

_PUBLIC_PAGES = [
    "/",
    "/tools/render",
    "/tools/pgn",
    "/tools/recognize",
    "/learn/fen",
    "/learn/pgn",
    "/about",
    "/admin/login",
]


def test_brand_and_font_assets_are_served(client: TestClient) -> None:
    for path, media_type in _STATIC_ASSETS:
        response = client.get(path)

        assert response.status_code == 200, path
        assert (
            response.headers["content-type"].split(";")[0] == media_type
        ), path
        assert response.content, path


def test_pages_declare_brand_icons_and_stylesheets(client: TestClient) -> None:
    for path in _PUBLIC_PAGES:
        page = client.get(path)

        assert page.status_code == 200, path
        assert 'name="theme-color" content="#3B2A17"' in page.text, path
        assert 'href="/static/brand/favicon.svg"' in page.text, path
        assert 'href="/static/brand/favicon.ico"' in page.text, path
        assert 'href="/static/brand/favicon-dark.ico"' in page.text, path
        assert 'href="/static/brand/icon-192.png"' in page.text, path
        assert 'href="/static/brand/icon-512.png"' in page.text, path
        assert 'href="/static/brand/apple-touch-icon.png"' in page.text, path
        assert 'href="/static/css/legacy.css"' in page.text, path
        assert 'href="/static/css/app.css"' in page.text, path


def test_about_uses_the_correct_image_namespace(client: TestClient) -> None:
    for lang in ("en", "zh", "de"):
        page = client.get("/about", headers={"Accept-Language": lang})

        assert page.status_code == 200, lang
        assert "ghcr.io/sidneylyzhang/chessfen" in page.text, lang
        assert "sidneylyzhang/chessfen" in page.text, lang
        assert _MISSPELLED_NAMESPACE not in page.text, lang
        assert "DOCKERHUB_USERNAME" not in page.text, lang