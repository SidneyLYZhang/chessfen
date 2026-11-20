from __future__ import annotations

from fastapi.testclient import TestClient

_EN = {"Accept-Language": "en"}
_ZH = {"Accept-Language": "zh"}
_DE = {"Accept-Language": "de"}

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

_NAV_LINKS = [
    "/tools/render",
    "/tools/pgn",
    "/tools/recognize",
    "/learn/fen",
    "/learn/pgn",
    "/about",
    "/admin",
]


def test_every_page_renders_the_brand_shell(client: TestClient) -> None:
    for path in _PUBLIC_PAGES:
        page = client.get(path, headers=_EN)

        assert page.status_code == 200, path
        assert 'class="skip-link" href="#main-content"' in page.text, path
        assert 'id="main-content"' in page.text, path
        assert page.text.count("<h1") == 1, path
        assert 'class="app-brand"' in page.text, path
        assert "/static/brand/mark.svg" in page.text, path
        assert '<span class="app-wordmark">chessfen</span>' in page.text, path
        for href in _NAV_LINKS:
            assert f'href="{href}"' in page.text, (path, href)
        for code in ("zh", "en", "de"):
            assert f'href="/lang/{code}?next=' in page.text, (path, code)
        assert "https://github.com/SidneyLYZhang/chessfen" in page.text, path
        assert "Open source under GPL-3.0-or-later" in page.text, path


def test_mobile_menu_is_server_rendered_and_wired(client: TestClient) -> None:
    page = client.get("/", headers=_EN)

    assert '<button class="app-menu-toggle"' in page.text
    assert 'data-menu-toggle' in page.text
    assert 'aria-controls="mobile-menu"' in page.text
    assert 'aria-expanded="false"' in page.text
    assert '<nav class="app-menu-panel" id="mobile-menu"' in page.text
    assert 'data-menu-panel' in page.text
    assert 'data-open="false"' in page.text
    assert 'aria-label="Main navigation"' in page.text
    for href in ("/tools/render", "/tools/pgn", "/tools/recognize", "/learn/fen"):
        assert page.text.count(f'href="{href}"') >= 2, href
    for label in ("中文", "English", "Deutsch"):
        assert label in page.text


def test_current_page_is_marked_in_desktop_and_mobile_nav(
    client: TestClient,
) -> None:
    page = client.get("/tools/render", headers=_EN)

    assert page.text.count('aria-current="page"') == 2


def test_shared_script_replaces_inline_copy_handler(client: TestClient) -> None:
    script = client.get("/static/js/app.js")

    assert script.status_code == 200
    assert "javascript" in script.headers["content-type"]
    assert "data-menu-toggle" in script.text
    assert "data-copy-target" in script.text
    assert "aria-expanded" in script.text

    page = client.get("/", headers=_EN)
    assert "/static/js/app.js" in page.text
    assert "function copyText" not in page.text
    assert "<script>" not in page.text


def test_home_hero_cards_and_open_strip(client: TestClient) -> None:
    page = client.get("/", headers=_EN)

    assert page.status_code == 200
    assert 'class="home-hero"' in page.text
    assert page.text.count("<h1") == 1
    assert "Recognize, parse, and render chess positions" in page.text
    assert 'class="primary-button" href="/tools/render"' in page.text
    assert 'class="secondary-button" href="/tools/recognize"' in page.text
    assert 'class="tool-card"' in page.text
    assert page.text.count('class="tool-card"') == 3
    assert 'class="open-strip"' in page.text
    assert "One Docker image" in page.text
    for href in ("/tools/render", "/tools/pgn", "/tools/recognize", "/about"):
        assert f'href="{href}"' in page.text, href


def test_home_title_and_description_are_localized(client: TestClient) -> None:
    en = client.get("/", headers=_EN)
    zh = client.get("/", headers=_ZH)
    de = client.get("/", headers=_DE)

    assert (
        "<title>ChessFEN · Chess encodings and position images</title>"
        in en.text
    )
    assert (
        'name="description" content="Render FEN positions, navigate PGN games '
        'to any Ply, and recognize Board images. ChessFEN is a small, '
        'self-hostable chess encoding tool."'
    ) in en.text
    assert "<title>ChessFEN · 国际象棋编码与局面图像</title>" in zh.text
    assert (
        'name="description" content="渲染 FEN 局面、把 PGN 对局导航到任意 Ply、'
        '识别棋盘图片。ChessFEN 是一个小巧、可自托管的国际象棋编码工具。"'
    ) in zh.text
    assert "<title>ChessFEN · Schachnotationen und Stellungsbilder</title>" in de.text
    assert (
        'name="description" content="FEN-Stellungen rendern, PGN-Partien bis '
        'zu jedem Ply durchlaufen und Brettbilder erkennen. ChessFEN ist ein '
        'kleines, selbst hostbares Werkzeug für Schachnotationen."'
    ) in de.text


def test_skip_link_and_menu_labels_are_localized(client: TestClient) -> None:
    zh = client.get("/", headers=_ZH)
    de = client.get("/", headers=_DE)

    assert "跳至主要内容" in zh.text
    assert "菜单" in zh.text
    assert "Zum Hauptinhalt springen" in de.text
    assert "Menü" in de.text
