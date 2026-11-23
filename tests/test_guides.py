from __future__ import annotations

import html
import re
from dataclasses import dataclass

from fastapi.testclient import TestClient

from chessfen.pgn import load_games
from chessfen.render import parse_fen

_EN = {"Accept-Language": "en"}
_ZH = {"Accept-Language": "zh"}
_DE = {"Accept-Language": "de"}


@dataclass(frozen=True)
class Guide:
    anchors: frozenset[str]
    rows: int
    tool: str
    other: str
    questions: tuple[str, ...]


_GUIDES: dict[str, Guide] = {
    "/learn/fen": Guide(
        anchors=frozenset({"piece-placement", "five-fields", "usage", "faq"}),
        rows=6,
        tool="/tools/render",
        other="/learn/pgn",
        questions=(
            "Is a Placement a Full FEN?",
            "Why is the side to move not recognized?",
            "Can I render illegal positions?",
        ),
    ),
    "/learn/pgn": Guide(
        anchors=frozenset(
            {"tags-and-moves", "plies", "mainline", "games", "faq"}
        ),
        rows=5,
        tool="/tools/pgn",
        other="/learn/fen",
        questions=(
            "Are variations included?",
            "What happens to a malformed PGN?",
            "How do I choose a specific half-move?",
        ),
    ),
}


def _code(page_text: str, code_id: str) -> str:
    match = re.search(
        rf'<code id="{code_id}">(.*?)</code>', page_text, re.DOTALL
    )
    assert match is not None, code_id
    return html.unescape(match.group(1)).strip()


def _guide_links(page_text: str) -> str:
    return page_text.split('class="guide-links"', 1)[1].split("</div>", 1)[0]


def _table_body(page_text: str) -> str:
    table = page_text.split('class="definition-table"', 1)[1]
    return table.split("<tbody>", 1)[1].split("</tbody>", 1)[0]


def test_guides_have_working_table_of_contents(client: TestClient) -> None:
    for path, expected in _GUIDES.items():
        page = client.get(path, headers=_EN)

        assert page.status_code == 200, path
        assert 'class="guide-toc"' in page.text, path
        assert 'class="guide-toc-mobile"' in page.text, path
        assert "On this page" in page.text, path

        anchors = {
            anchor
            for anchor in re.findall(r'href="#([^"]+)"', page.text)
            if anchor != "main-content"
        }
        assert anchors == expected.anchors, path
        for anchor in expected.anchors:
            assert f'id="{anchor}"' in page.text, (path, anchor)


def test_guides_have_copyable_code_blocks(client: TestClient) -> None:
    fen = client.get("/learn/fen", headers=_EN)
    placement = _code(fen.text, "fen-placement-code")
    full_fen = _code(fen.text, "fen-full-code")

    assert placement == "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR"
    assert full_fen == f"{placement} w KQkq - 0 1"
    assert parse_fen(placement).fen().startswith(placement)
    assert parse_fen(full_fen).fen() == full_fen
    assert 'data-copy-target="fen-placement-code"' in fen.text
    assert 'data-copy-target="fen-full-code"' in fen.text
    assert "Copy" in fen.text

    pgn = client.get("/learn/pgn", headers=_EN)
    sample = load_games(_code(pgn.text, "pgn-sample-code"))
    variation = load_games(_code(pgn.text, "pgn-variation-code"))

    assert len(sample) == 1
    assert len(variation) == 1
    board = variation[0].board()
    moves = []
    for move in variation[0].mainline_moves():
        moves.append(board.san(move))
        board.push(move)
    assert moves == ["e4", "e5", "Nf3"]
    assert 'data-copy-target="pgn-sample-code"' in pgn.text
    assert 'data-copy-target="pgn-variation-code"' in pgn.text


def test_guides_include_definition_tables(client: TestClient) -> None:
    for path, expected in _GUIDES.items():
        page = client.get(path, headers=_EN)

        assert 'class="definition-table"' in page.text, path
        assert "Term" in page.text, path
        assert "Example" in page.text, path
        assert "Meaning" in page.text, path

        body = _table_body(page.text)
        assert body.count("<tr>") == expected.rows, path

    fen = client.get("/learn/fen", headers=_EN)
    for term in (
        "Placement",
        "Side to move",
        "Castling rights",
        "En passant",
        "Halfmove clock",
        "Fullmove number",
    ):
        assert term in fen.text

    pgn = client.get("/learn/pgn", headers=_EN)
    for term in ("Tag pair", "Move number", "Ply", "Variation", "Result"):
        assert term in pgn.text


def test_guides_have_native_faq_disclosures(client: TestClient) -> None:
    for path, expected in _GUIDES.items():
        page = client.get(path, headers=_EN)

        assert 'class="guide-faq"' in page.text, path
        faq = page.text.split('class="guide-faq"', 1)[1].split(
            "</section>", 1
        )[0]
        assert faq.count("<details>") == 3, path
        for question in expected.questions:
            assert f"<summary>{question}</summary>" in faq, (
                path,
                question,
            )


def test_guides_cross_link_to_each_other_and_their_tools(
    client: TestClient,
) -> None:
    for path, expected in _GUIDES.items():
        links = _guide_links(client.get(path, headers=_EN).text)

        assert f'href="{expected.tool}"' in links, path
        assert f'href="{expected.other}"' in links, path


def test_guides_are_localized(client: TestClient) -> None:
    fen_zh = client.get("/learn/fen", headers=_ZH)
    fen_de = client.get("/learn/fen", headers=_DE)
    pgn_zh = client.get("/learn/pgn", headers=_ZH)
    pgn_de = client.get("/learn/pgn", headers=_DE)

    assert "本页内容" in fen_zh.text
    assert "常见问题" in fen_zh.text
    assert "术语" in fen_zh.text
    assert "继续阅读 PGN 指南" in fen_zh.text
    assert "Auf dieser Seite" in fen_de.text
    assert "Häufige Fragen" in fen_de.text
    assert "Begriff" in fen_de.text
    assert "Weiter zum PGN-Leitfaden" in fen_de.text
    assert "继续阅读 FEN 指南" in pgn_zh.text
    assert "Weiter zum FEN-Leitfaden" in pgn_de.text
