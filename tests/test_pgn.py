from __future__ import annotations

import base64
import io
import re

import pytest
from fastapi.testclient import TestClient
from PIL import Image

_EN = {"Accept-Language": "en"}
_START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
_G0_PLY1 = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"
_G0_PLY2 = "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2"
_G0_PLY3 = "rnbqkbnr/pppp1ppp/8/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R b KQkq - 1 2"
_G1_PLY2 = "rnbqkbnr/ppp1pppp/8/3p4/3P4/8/PPP1PPPP/RNBQKBNR w KQkq - 0 2"

_FEN_START_BLACK = "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"
_FEN_AFTER_NF6 = "rnbqkb1r/pppp1ppp/5n2/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 1 2"
_FEN_AFTER_NF3 = "rnbqkb1r/pppp1ppp/5n2/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R b KQkq - 2 2"

_BLACK_START_GAME = '''[Event "FEN start"]
[SetUp "1"]
[FEN "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"]
[Result "*"]

1... Nf6 2. Nf3 *
'''

_BLACK_START_MOVE20_GAME = '''[Event "FEN start at move 20"]
[SetUp "1"]
[FEN "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 20"]
[Result "*"]

20... Nf6 21. Nf3 *
'''

_TWO_GAMES = '''[Event "Test"]
[White "Alice"]
[Black "Bob"]
[Result "1-0"]

1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 1-0

[Event "Test"]
[White "Carol"]
[Black "Dave"]
[Result "0-1"]

1. d4 d5 2. c4 e6 0-1
'''


def _pgn(client: TestClient, api_key: str, **overrides):
    payload = {"pgn": _TWO_GAMES}
    payload.update(overrides)
    return client.post(
        "/api/v1/pgn", json=payload, headers={"X-API-Key": api_key}
    )


def _data_uri_png(page_text: str) -> Image.Image:
    match = re.search(r"data:image/png;base64,([A-Za-z0-9+/=]+)", page_text)
    assert match is not None
    return Image.open(io.BytesIO(base64.b64decode(match.group(1)))).convert(
        "RGB"
    )


def test_api_ply_zero_is_the_initial_position(
    client: TestClient, api_key: str
) -> None:
    response = _pgn(client, api_key, game_index=0, ply=0)

    assert response.status_code == 200
    assert response.json()["fen"] == _START


def test_api_navigates_white_and_black_half_moves(
    client: TestClient, api_key: str
) -> None:
    after_white1 = _pgn(client, api_key, game_index=0, ply=1)
    after_black1 = _pgn(client, api_key, game_index=0, ply=2)
    after_white2 = _pgn(client, api_key, game_index=0, ply=3)

    assert after_white1.json()["fen"] == _G0_PLY1
    assert after_black1.json()["fen"] == _G0_PLY2
    assert after_white2.json()["fen"] == _G0_PLY3


def test_api_selects_other_games(client: TestClient, api_key: str) -> None:
    response = _pgn(client, api_key, game_index=1, ply=2)

    assert response.status_code == 200
    assert response.json()["fen"] == _G1_PLY2
    assert response.json()["available_plies"] == 4


def test_api_follows_mainline_and_ignores_variations(
    client: TestClient, api_key: str
) -> None:
    response = _pgn(
        client, api_key, pgn="1. e4 (1. d4 d5) e5 *", game_index=0, ply=2
    )

    assert response.status_code == 200
    assert response.json()["fen"] == _G0_PLY2


def test_api_rejects_out_of_range_ply(client: TestClient, api_key: str) -> None:
    response = _pgn(client, api_key, game_index=0, ply=99)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "ply_out_of_range"


def test_api_rejects_unknown_game(client: TestClient, api_key: str) -> None:
    response = _pgn(client, api_key, game_index=5, ply=0)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_game"


def test_api_rejects_garbage_pgn(client: TestClient, api_key: str) -> None:
    response = _pgn(
        client, api_key, pgn="this is not a pgn", game_index=0, ply=0
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_pgn"


def test_api_rejects_pgn_with_illegal_moves(
    client: TestClient, api_key: str
) -> None:
    response = _pgn(client, api_key, pgn="1. e5 e4", game_index=0, ply=0)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_pgn"


def test_api_rejects_pgn_when_a_variation_corrupts_the_mainline(
    client: TestClient, api_key: str
) -> None:
    # python-chess records errors from variations and can corrupt the parsed
    # mainline afterwards, so the document is rejected rather than serving a
    # possibly wrong Position.
    response = _pgn(
        client, api_key, pgn="1. e4 (1. e5 e4) e5 *", game_index=0, ply=1
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_pgn"


def test_api_pgn_requires_key(client: TestClient) -> None:
    response = client.post("/api/v1/pgn", json={"pgn": _TWO_GAMES})

    assert response.status_code == 401


def test_pgn_page_serves_form_in_chinese(client: TestClient) -> None:
    page = client.get("/tools/pgn")

    assert page.status_code == 200
    assert "PGN 导航" in page.text


def test_pgn_page_renders_in_german(client: TestClient) -> None:
    page = client.get("/tools/pgn", headers={"Accept-Language": "de"})

    assert "PGN-Navigation" in page.text


def test_pgn_form_lists_multiple_games(client: TestClient) -> None:
    page = client.post("/tools/pgn", data={"pgn": _TWO_GAMES}, headers=_EN)

    assert page.status_code == 200
    assert "Alice vs Bob" in page.text
    assert "Carol vs Dave" in page.text
    assert "Only the mainline is followed" in page.text


@pytest.mark.parametrize(
    ("side", "expected_fen"), [("white", _G0_PLY1), ("black", _G0_PLY2)]
)
def test_pgn_form_exports_fen_for_half_move(
    client: TestClient, side: str, expected_fen: str
) -> None:
    page = client.post(
        "/tools/pgn",
        data={
            "pgn": _TWO_GAMES,
            "game_index": "0",
            "move_number": "1",
            "side": side,
        },
        headers=_EN,
    )

    assert page.status_code == 200
    assert expected_fen in page.text


def test_pgn_form_offers_copy_button(client: TestClient) -> None:
    page = client.post(
        "/tools/pgn",
        data={
            "pgn": _TWO_GAMES,
            "game_index": "0",
            "move_number": "1",
            "side": "black",
        },
        headers=_EN,
    )

    assert page.status_code == 200
    assert 'data-copy-target="fen-text"' in page.text
    assert "Copy FEN" in page.text
    assert 'id="fen-text"' in page.text
    assert "Full FEN" in page.text


def test_pgn_form_move_zero_shows_initial_position(client: TestClient) -> None:
    page = client.post(
        "/tools/pgn",
        data={
            "pgn": _TWO_GAMES,
            "game_index": "0",
            "move_number": "0",
            "side": "white",
        },
        headers=_EN,
    )

    assert page.status_code == 200
    assert _START in page.text


def test_pgn_form_reports_out_of_range_move(client: TestClient) -> None:
    page = client.post(
        "/tools/pgn",
        data={
            "pgn": _TWO_GAMES,
            "game_index": "0",
            "move_number": "99",
            "side": "white",
        },
        headers=_EN,
    )

    assert page.status_code == 400
    assert "That move does not exist in this game." in page.text


def test_pgn_form_reports_invalid_pgn(client: TestClient) -> None:
    page = client.post("/tools/pgn", data={"pgn": "garbage"}, headers=_EN)

    assert page.status_code == 400
    assert "Invalid PGN" in page.text


def test_pgn_form_reports_invalid_move_number(client: TestClient) -> None:
    page = client.post(
        "/tools/pgn",
        data={
            "pgn": _TWO_GAMES,
            "game_index": "0",
            "move_number": "abc",
            "side": "white",
        },
        headers=_EN,
    )

    assert page.status_code == 400
    assert "The move number is invalid." in page.text


def test_pgn_form_accepts_file_upload(client: TestClient) -> None:
    page = client.post(
        "/tools/pgn",
        data={"game_index": "0", "move_number": "1", "side": "black"},
        files={
            "pgn_file": ("games.pgn", _TWO_GAMES.encode("utf-8"), "text/plain")
        },
        headers=_EN,
    )

    assert page.status_code == 200
    assert "Carol vs Dave" in page.text
    assert _G0_PLY2 in page.text


def test_home_links_to_pgn_tool(client: TestClient) -> None:
    page = client.get("/", headers=_EN)

    assert "/tools/pgn" in page.text


def test_pgn_page_starts_with_empty_workspace(client: TestClient) -> None:
    page = client.get("/tools/pgn", headers=_EN)

    assert page.status_code == 200
    assert "data:image" not in page.text
    assert "The board appears here." in page.text
    assert "Paste a PGN and open a game to preview positions." in page.text
    assert 'name="pgn"' in page.text
    assert 'placeholder="Paste one or more games."' in page.text
    assert 'name="mode" value="parse"' in page.text
    assert "Only the mainline is followed" in page.text
    assert "Upload a PGN, pick a Game" in page.text


def test_pgn_parse_lists_games_without_a_board(client: TestClient) -> None:
    page = client.post("/tools/pgn", data={"pgn": _TWO_GAMES}, headers=_EN)

    assert page.status_code == 200
    assert "Alice vs Bob" in page.text
    assert "Carol vs Dave" in page.text
    assert 'name="game_index"' in page.text
    assert 'name="mode" value="open"' in page.text
    assert "data:image" not in page.text
    assert 'name="ply"' not in page.text


def test_pgn_open_game_shows_board_and_move_grid(
    client: TestClient,
) -> None:
    page = client.post(
        "/tools/pgn",
        data={"pgn": _TWO_GAMES, "game_index": "0", "mode": "open"},
        headers=_EN,
    )

    assert page.status_code == 200
    assert "data:image/png;base64," in page.text
    assert _START in page.text
    assert "Alice vs Bob" in page.text
    assert "0 / 6" in page.text
    assert "Initial position" in page.text
    for san in ("e4", "e5", "Nf3", "Nc6", "Bb5", "a6"):
        assert f">{san}</button>" in page.text
    assert 'value="0" aria-current="true">Initial position</button>' in page.text


def test_pgn_move_grid_navigates_to_a_ply(client: TestClient) -> None:
    page = client.post(
        "/tools/pgn",
        data={"pgn": _TWO_GAMES, "game_index": "0", "ply": "3"},
        headers=_EN,
    )

    assert page.status_code == 200
    assert _G0_PLY3 in page.text
    assert 'value="3" aria-current="true">Nf3</button>' in page.text
    assert re.search(r'id="move_number"[^>]*value="2"', page.text)
    assert '<option value="white" selected>White</option>' in page.text
    assert "3 / 6" in page.text


def test_pgn_precise_controls_agree_with_the_grid(client: TestClient) -> None:
    page = client.post(
        "/tools/pgn",
        data={
            "pgn": _TWO_GAMES,
            "game_index": "0",
            "move_number": "2",
            "side": "white",
            "mode": "locate",
        },
        headers=_EN,
    )

    assert page.status_code == 200
    assert _G0_PLY3 in page.text
    assert 'value="3" aria-current="true">Nf3</button>' in page.text


def test_pgn_move_grid_returns_to_initial_position(client: TestClient) -> None:
    page = client.post(
        "/tools/pgn",
        data={"pgn": _TWO_GAMES, "game_index": "0", "ply": "0"},
        headers=_EN,
    )

    assert page.status_code == 200
    assert _START in page.text
    assert 'value="0" aria-current="true">Initial position</button>' in page.text
    assert re.search(r'id="move_number"[^>]*value="0"', page.text)


def test_pgn_selects_other_games_from_the_grid(client: TestClient) -> None:
    opened = client.post(
        "/tools/pgn",
        data={"pgn": _TWO_GAMES, "game_index": "1", "mode": "open"},
        headers=_EN,
    )
    assert "Carol vs Dave" in opened.text
    assert '<option value="1" selected>' in opened.text

    board = client.post(
        "/tools/pgn",
        data={"pgn": _TWO_GAMES, "game_index": "1", "ply": "2"},
        headers=_EN,
    )
    assert _G1_PLY2 in board.text
    assert 'value="2" aria-current="true">d5</button>' in board.text


def test_pgn_board_uses_brand_palette(client: TestClient) -> None:
    page = client.post(
        "/tools/pgn",
        data={"pgn": _TWO_GAMES, "game_index": "0", "mode": "open"},
        headers=_EN,
    )

    pixels = set(_data_uri_png(page.text).get_flattened_data())
    assert (0xF3, 0xF0, 0xE8) in pixels
    assert (0xBF, 0x81, 0x1D) in pixels


def test_pgn_new_labels_are_localized(client: TestClient) -> None:
    zh = client.post(
        "/tools/pgn",
        data={"pgn": _TWO_GAMES, "game_index": "0", "mode": "open"},
        headers={"Accept-Language": "zh"},
    )
    de = client.post(
        "/tools/pgn",
        data={"pgn": _TWO_GAMES, "game_index": "0", "mode": "open"},
        headers={"Accept-Language": "de"},
    )

    assert "打开对局" in zh.text
    assert "初始局面" in zh.text
    assert "半步" in zh.text
    assert "主线着法" in zh.text
    assert "Partie öffnen" in de.text
    assert "Grundstellung" in de.text
    assert "Halbzug" in de.text
    assert "Züge der Hauptvariante" in de.text


def test_pgn_errors_are_announced(client: TestClient) -> None:
    invalid = client.post("/tools/pgn", data={"pgn": "garbage"}, headers=_EN)
    assert invalid.status_code == 400
    assert 'role="alert"' in invalid.text

    out_of_range = client.post(
        "/tools/pgn",
        data={
            "pgn": _TWO_GAMES,
            "game_index": "0",
            "move_number": "99",
            "side": "white",
        },
        headers=_EN,
    )
    assert out_of_range.status_code == 400
    assert 'role="alert"' in out_of_range.text


def test_pgn_handles_fen_start_with_black_to_move(client: TestClient) -> None:
    opened = client.post(
        "/tools/pgn",
        data={"pgn": _BLACK_START_GAME, "game_index": "0", "mode": "open"},
        headers=_EN,
    )

    assert opened.status_code == 200
    assert _FEN_START_BLACK in opened.text
    assert ">Nf6</button>" in opened.text
    assert ">Nf3</button>" in opened.text
    assert '<option value="black" selected>Black</option>' in opened.text
    assert re.search(
        r'<span class="pgn-move-number">1\.</span>\s*<span></span>\s*'
        r'<button[^>]*value="1"[^>]*>Nf6</button>',
        opened.text,
    )

    first = client.post(
        "/tools/pgn",
        data={"pgn": _BLACK_START_GAME, "game_index": "0", "ply": "1"},
        headers=_EN,
    )
    assert _FEN_AFTER_NF6 in first.text
    assert 'value="1" aria-current="true">Nf6</button>' in first.text
    assert re.search(r'id="move_number"[^>]*value="1"', first.text)
    assert '<option value="black" selected>Black</option>' in first.text
    assert "<dt>Side to move</dt><dd>White</dd>" in first.text

    precise_black = client.post(
        "/tools/pgn",
        data={
            "pgn": _BLACK_START_GAME,
            "game_index": "0",
            "move_number": "1",
            "side": "black",
            "mode": "locate",
        },
        headers=_EN,
    )
    assert _FEN_AFTER_NF6 in precise_black.text
    assert 'value="1" aria-current="true">Nf6</button>' in precise_black.text

    precise_white = client.post(
        "/tools/pgn",
        data={
            "pgn": _BLACK_START_GAME,
            "game_index": "0",
            "move_number": "2",
            "side": "white",
            "mode": "locate",
        },
        headers=_EN,
    )
    assert _FEN_AFTER_NF3 in precise_white.text
    assert 'value="2" aria-current="true">Nf3</button>' in precise_white.text
    assert re.search(r'id="move_number"[^>]*value="2"', precise_white.text)
    assert '<option value="white" selected>White</option>' in precise_white.text
    assert "<dt>Side to move</dt><dd>Black</dd>" in precise_white.text

    grid = client.post(
        "/tools/pgn",
        data={"pgn": _BLACK_START_GAME, "game_index": "0", "ply": "2"},
        headers=_EN,
    )
    assert _FEN_AFTER_NF3 in grid.text
    assert 'value="2" aria-current="true">Nf3</button>' in grid.text
    assert re.search(r'id="move_number"[^>]*value="2"', grid.text)
    assert '<option value="white" selected>White</option>' in grid.text

    beyond = client.post(
        "/tools/pgn",
        data={
            "pgn": _BLACK_START_GAME,
            "game_index": "0",
            "move_number": "1",
            "side": "white",
            "mode": "locate",
        },
        headers=_EN,
    )
    assert beyond.status_code == 400
    assert "That move does not exist in this game." in beyond.text


def test_pgn_precise_controls_keep_fen_fullmove_number(
    client: TestClient,
) -> None:
    first = client.post(
        "/tools/pgn",
        data={
            "pgn": _BLACK_START_MOVE20_GAME,
            "game_index": "0",
            "move_number": "20",
            "side": "black",
            "mode": "locate",
        },
        headers=_EN,
    )

    assert first.status_code == 200
    assert _FEN_AFTER_NF6 in first.text
    assert re.search(r'id="move_number"[^>]*value="20"', first.text)
    assert '<option value="black" selected>Black</option>' in first.text

    second = client.post(
        "/tools/pgn",
        data={
            "pgn": _BLACK_START_MOVE20_GAME,
            "game_index": "0",
            "move_number": "21",
            "side": "white",
            "mode": "locate",
        },
        headers=_EN,
    )

    assert second.status_code == 200
    assert _FEN_AFTER_NF3 in second.text
    assert 'value="2" aria-current="true">Nf3</button>' in second.text
    assert re.search(r'id="move_number"[^>]*value="21"', second.text)
    assert '<option value="white" selected>White</option>' in second.text


def test_pgn_rejects_invalid_and_out_of_range_ply(
    client: TestClient,
) -> None:
    invalid = client.post(
        "/tools/pgn",
        data={"pgn": _TWO_GAMES, "game_index": "0", "ply": "abc"},
        headers=_EN,
    )
    assert invalid.status_code == 400
    assert 'role="alert"' in invalid.text
    assert "The Ply is invalid." in invalid.text

    out_of_range = client.post(
        "/tools/pgn",
        data={"pgn": _TWO_GAMES, "game_index": "0", "ply": "99"},
        headers=_EN,
    )
    assert out_of_range.status_code == 400
    assert 'role="alert"' in out_of_range.text
    assert "That move does not exist in this game." in out_of_range.text
