from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

_EN = {"Accept-Language": "en"}
_START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
_G0_PLY1 = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"
_G0_PLY2 = "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2"
_G0_PLY3 = "rnbqkbnr/pppp1ppp/8/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R b KQkq - 1 2"
_G1_PLY2 = "rnbqkbnr/ppp1pppp/8/3p4/3P4/8/PPP1PPPP/RNBQKBNR w KQkq - 0 2"

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
