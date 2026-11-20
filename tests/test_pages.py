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


def test_about_lists_star_badge_and_self_deploy(client: TestClient) -> None:
    page = client.get("/about", headers=_EN)

    assert 'class="badge"' in page.text
    assert "Star on GitHub" in page.text
    assert "https://github.com/SidneyLYZhang/chessfen" in page.text
    assert "ghcr.io/sidneylyzhang/chessfen" in page.text
    assert "sidneylyzhang/chessfen" in page.text
    assert "docker run" in page.text
    assert "docker compose up -d" in page.text
    assert "ADMIN_PASSWORD=" in page.text
    assert "2d-chess-ocr" in page.text
    assert "CBurnett" in page.text
    assert "GPL-3.0" in page.text


def test_about_explains_tls_and_postgres_options(client: TestClient) -> None:
    page = client.get("/about", headers=_EN)

    assert "reverse proxy" in page.text
    assert "DATABASE_URL" in page.text


def test_footer_states_the_license(client: TestClient) -> None:
    zh = client.get("/", headers=_ZH)
    en = client.get("/", headers=_EN)

    assert "GPL-3.0-or-later 开源" in zh.text
    assert "Open source under GPL-3.0-or-later" in en.text
