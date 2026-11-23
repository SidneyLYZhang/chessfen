from __future__ import annotations

from fastapi.testclient import TestClient

_ZH = {"Accept-Language": "zh"}
_EN = {"Accept-Language": "en"}
_DE = {"Accept-Language": "de"}


def test_header_links_to_learning_pages(client: TestClient) -> None:
    page = client.get("/", headers=_EN)

    assert 'href="/learn/fen"' in page.text
    assert 'href="/learn/pgn"' in page.text
    assert 'href="/about"' in page.text


def test_fen_intro_renders_in_three_languages(client: TestClient) -> None:
    zh = client.get("/learn/fen", headers=_ZH)
    en = client.get("/learn/fen", headers=_EN)
    de = client.get("/learn/fen", headers=_DE)

    assert "理解 FEN" in zh.text
    assert "棋子摆放段开头" in zh.text
    assert "Understanding FEN" in en.text
    assert "A FEN string starts with" in en.text
    assert "FEN verstehen" in de.text
    assert "Ein FEN-String beginnt" in de.text


def test_pgn_intro_renders_in_three_languages(client: TestClient) -> None:
    zh = client.get("/learn/pgn", headers=_ZH)
    en = client.get("/learn/pgn", headers=_EN)
    de = client.get("/learn/pgn", headers=_DE)

    assert "理解 PGN" in zh.text
    assert "括号中是变例" in zh.text
    assert "整份 PGN 都会被拒绝" in zh.text
    assert "Understanding PGN" in en.text
    assert "Parentheses contain variations" in en.text
    assert "PGN verstehen" in de.text
    assert "Klammern enthalten Varianten" in de.text


def test_about_page_renders_in_three_languages(client: TestClient) -> None:
    zh = client.get("/about", headers=_ZH)
    en = client.get("/about", headers=_EN)
    de = client.get("/about", headers=_DE)

    assert "关于 ChessFEN" in zh.text
    assert "发布镜像已发布到 GHCR" in zh.text
    assert "任意棋子摆放段或完整 FEN" in zh.text
    assert "About ChessFEN" in en.text
    assert "Release images are published to GHCR" in en.text
    assert "Über ChessFEN" in de.text
    assert "Veröffentlichte Release-Images stehen auf GHCR" in de.text


def test_about_lists_star_cta_and_self_deploy(client: TestClient) -> None:
    page = client.get("/about", headers=_EN)

    assert 'class="primary-button"' in page.text
    assert "Star on GitHub" in page.text
    assert "https://github.com/SidneyLYZhang/chessfen" in page.text
    assert "/static/brand/mark.svg" in page.text
    assert "/static/brand/mark-reverse.svg" in page.text
    assert 'data-copy-target="docker-run-code"' in page.text
    assert 'id="docker-run-code"' in page.text
    assert "ghcr.io/sidneylyzhang/chessfen" in page.text
    assert "sidneylyzhang/chessfen" in page.text
    assert "docker run" in page.text
    assert "docker compose up -d" in page.text
    assert "docker pull sidneylyzhang/chessfen:latest" in page.text
    assert "ADMIN_PASSWORD=" in page.text
    assert "SECRET_KEY=" in page.text
    assert "-v chessfen-data:/data" in page.text
    assert "127.0.0.1:8000:8000" in page.text
    assert "2d-chess-ocr" in page.text
    assert "CBurnett" in page.text
    assert "GPL-3.0" in page.text


def test_about_explains_tls_and_postgres_options(client: TestClient) -> None:
    page = client.get("/about", headers=_EN)

    assert "reverse proxy" in page.text
    assert "DATABASE_URL" in page.text


def test_about_has_brand_hero_and_principles(client: TestClient) -> None:
    page = client.get("/about", headers=_EN)

    assert 'class="about-hero"' in page.text
    assert 'class="brand-panel"' in page.text
    assert "FEN ↔ PGN ↔ IMAGE" in page.text
    assert page.text.count('class="principle"') == 3
    assert "Encodings first" in page.text
    assert "One image, CPU-only" in page.text
    assert "Open source" in page.text
    assert 'class="about-self-host"' in page.text

    hero = page.text.split('class="about-hero"', 1)[1].split(
        "</header>", 1
    )[0]
    assert "/static/brand/mark.svg" in hero
    assert 'class="brand-panel"' in hero

    dark = page.text.split('class="about-self-host"', 1)[1]
    assert "/static/brand/mark-reverse.svg" in dark
    assert "docker pull sidneylyzhang/chessfen:latest" in dark
    assert 'data-copy-target="dockerhub-pull-code"' in dark
    assert "postgresql+psycopg" in dark


def test_about_principles_are_localized(client: TestClient) -> None:
    zh = client.get("/about", headers=_ZH)
    de = client.get("/about", headers=_DE)

    assert "编码优先" in zh.text
    assert "单镜像，纯 CPU" in zh.text
    assert "开源" in zh.text
    assert "Kodierungen zuerst" in de.text
    assert "Ein Image, nur CPU" in de.text
    assert "Erkennungsmodell" in de.text


def test_footer_states_the_license(client: TestClient) -> None:
    zh = client.get("/", headers=_ZH)
    en = client.get("/", headers=_EN)

    assert "GPL-3.0-or-later 开源" in zh.text
    assert "Open source under GPL-3.0-or-later" in en.text


def test_unknown_page_returns_localized_404(client: TestClient) -> None:
    en = client.get("/no-such-page", headers=_EN)

    assert en.status_code == 404
    assert "Page not found" in en.text
    assert "Go home" in en.text
    assert 'class="app-header"' in en.text
    for href in ("/", "/tools/render", "/tools/pgn", "/tools/recognize"):
        assert f'href="{href}"' in en.text

    zh = client.get("/no-such-page", headers=_ZH)
    assert zh.status_code == 404
    assert "页面未找到" in zh.text
    assert "返回首页" in zh.text

    de = client.get("/no-such-page", headers=_DE)
    assert de.status_code == 404
    assert "Seite nicht gefunden" in de.text
    assert "Zur Startseite" in de.text


def test_static_missing_path_stays_non_html(client: TestClient) -> None:
    response = client.get("/static/no-such-file.css")

    assert response.status_code == 404
    assert "Page not found" not in response.text


def test_bare_api_path_stays_json(client: TestClient) -> None:
    response = client.get("/api")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert "Page not found" not in response.text
