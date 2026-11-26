from __future__ import annotations

import chess
import chess.pgn
import pytest
from fastapi.testclient import TestClient

_EN = {"Accept-Language": "en"}
_ZH = {"Accept-Language": "zh"}
_DE = {"Accept-Language": "de"}

_ORIGIN = {"Origin": "http://testserver"}
_START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
_BLACK_TO_MOVE_FEN = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"
_CHECKMATE_FEN = "rnb1kbnr/pppp1ppp/8/4p3/6Pq/5P2/PPPPP2P/RNBQKBNR w KQkq - 1 3"
_STALEMATE_FEN = "7k/5Q2/6K1/8/8/8/8/8 b - - 0 1"
_INSUFFICIENT_FEN = "8/8/8/8/8/8/3k4/7K w - - 0 1"
_SEVENTYFIVE_MOVE_FEN = "8/8/8/8/8/2k5/4P3/4K3 w - - 150 200"
_AFTER_E4_FEN = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"
_CASTLE_FEN = "r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1"
_PROMOTION_FEN = "8/P6k/8/8/8/8/8/7K w - - 0 1"
_CHECK_FEN = "7k/8/8/8/8/8/8/R6K w - - 0 1"
_FORCED_CAPTURE_FEN = "7k/8/8/8/8/8/1q6/K7 w - - 0 1"


def _analysis_post(
    client: TestClient, path: str, payload: dict[str, object]
):
    return client.post(
        path,
        json=payload,
        headers={**_EN, **_ORIGIN},
    )


def test_analysis_workbench_is_localized_and_uses_scoped_assets(
    client: TestClient,
) -> None:
    expected_headings = {
        "en": "Position analysis",
        "zh": "局面分析",
        "de": "Stellungsanalyse",
    }

    for language, heading in expected_headings.items():
        page = client.get(
            "/tools/analysis", headers={"Accept-Language": language}
        )

        assert page.status_code == 200, language
        assert f"<h1>{heading}</h1>" in page.text, language
        assert 'aria-current="page"' in page.text, language
        assert page.text.count('href="/tools/analysis"') >= 2, language
        assert "<noscript>" in page.text, language

    page = client.get("/tools/analysis", headers=_EN)
    assert '<script src="/static/js/app.js"></script>' in page.text
    assert '<script type="module" src="/static/js/analysis.js"></script>' in page.text
    assert page.text.count("<script") == 3
    assert "<script>" not in page.text
    assert "Full FEN" in page.text
    assert "Placement only is not enough for analysis." in page.text
    assert "FEN alone does not identify a previous Ply." in page.text
    assert "Positive Evaluation favors White; negative favors Black." in page.text
    assert page.headers["cache-control"] == "no-store"
    assert "script-src 'self' 'wasm-unsafe-eval'" in page.headers[
        "content-security-policy"
    ]
    assert "worker-src 'self'" in page.headers["content-security-policy"]
    assert page.headers["cross-origin-opener-policy"] == "same-origin"
    assert page.headers["cross-origin-embedder-policy"] == "require-corp"
    for path in ("/", "/settings", "/tools/pgn"):
        other = client.get(path, headers=_EN)
        assert "cross-origin-opener-policy" not in other.headers
        assert "cross-origin-embedder-policy" not in other.headers

    zh = client.get("/tools/analysis", headers=_ZH)
    de = client.get("/tools/analysis", headers=_DE)
    assert "仅棋盘布局不足以进行分析。" in zh.text
    assert "Nur die Brettaufstellung reicht für eine Analyse nicht aus." in de.text
    assert "请使用包含六个字段的 Full FEN。" in zh.text
    assert "Verwende eine Full FEN mit sechs Feldern." in de.text
    assert 'aria-label="引擎详情"' in zh.text
    assert 'aria-label="Engine-Details"' in de.text
    assert "Actual move" in page.text
    assert "Move assessment" in page.text
    assert "实际走法" in zh.text
    assert "该走法在此局面中不合法。" in zh.text
    assert "将杀损失" in zh.text
    assert "Zugbewertung" in de.text
    assert "Dieser Zug ist in dieser Stellung nicht legal." in de.text
    assert "Mattverlust" in de.text


def test_existing_fen_and_pgn_tools_link_to_analysis(
    client: TestClient,
) -> None:
    for path in ("/tools/render", "/tools/pgn"):
        page = client.get(path, headers=_EN)

        assert page.status_code == 200, path
        assert ">Open Analysis" in page.text, path
        assert 'href="/tools/analysis"' in page.text, path


def test_analysis_workspaces_expose_local_engine_settings(
    client: TestClient,
) -> None:
    for path in ("/tools/analysis", "/tools/analysis/pgn"):
        page = client.get(path, headers=_EN)

        assert 'data-setting-preset' in page.text, path
        assert 'data-setting-multipv' in page.text, path
        assert 'data-setting-resource' in page.text, path
        assert "Stockfish 19 · Lite" in page.text, path


def test_analysis_position_requires_same_origin(client: TestClient) -> None:
    response = client.post(
        "/tools/analysis/position",
        json={"fen": _START_FEN},
        headers=_EN,
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "invalid_origin"


def test_analysis_position_rejects_cross_site_fetch_metadata(
    client: TestClient,
) -> None:
    response = client.post(
        "/tools/analysis/position",
        json={"fen": _START_FEN},
        headers={**_EN, **_ORIGIN, "Sec-Fetch-Site": "cross-site"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "invalid_origin"


def test_analysis_routes_reject_oversized_bodies(client: TestClient) -> None:
    response = client.post(
        "/tools/analysis/position",
        content=b"x" * (64 * 1024 + 1),
        headers={
            **_EN,
            **_ORIGIN,
            "Content-Type": "application/json",
        },
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "request_too_large"


def test_analysis_position_rejects_placement_only(client: TestClient) -> None:
    response = _analysis_post(
        client,
        "/tools/analysis/position",
        {"fen": "8/8/8/8/8/8/4K3/7k"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "placement_only"


def test_analysis_position_rejects_malformed_and_illegal_positions(
    client: TestClient,
) -> None:
    malformed = _analysis_post(
        client, "/tools/analysis/position", {"fen": "not a position"}
    )
    illegal = _analysis_post(
        client, "/tools/analysis/position", {"fen": "8/8/8/8/8/8/8/8 w - - 0 1"}
    )

    assert malformed.status_code == 400
    assert malformed.json()["error"]["code"] == "invalid_fen"
    assert illegal.status_code == 400
    assert illegal.json()["error"]["code"] == "illegal_position"


def test_analysis_position_returns_a_legal_full_fen(client: TestClient) -> None:
    response = _analysis_post(
        client, "/tools/analysis/position", {"fen": _START_FEN}
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["fen"] == _START_FEN
    assert payload["side_to_move"] == "white"
    assert payload["terminal"] is None
    assert payload["board_uri"].startswith("data:image/svg+xml;base64,")


@pytest.mark.parametrize("move", ["e4", "e2e4"])
def test_analysis_position_accepts_an_actual_san_or_uci_move(
    client: TestClient, move: str
) -> None:
    response = _analysis_post(
        client,
        "/tools/analysis/position",
        {"fen": _START_FEN, "move": move},
    )

    assert response.status_code == 200
    payload = response.json()
    actual = payload["actual_move"]
    assert actual["uci"] == "e2e4"
    assert actual["san"] == "e4"
    assert actual["before_fen"] == _START_FEN
    assert actual["after_fen"] == _AFTER_E4_FEN
    assert actual["piece"] == "pawn"
    assert actual["captured_piece"] is None
    assert actual["captured_color"] is None
    assert actual["promotion"] is None
    assert actual["is_capture"] is False
    assert actual["is_check"] is False
    assert actual["is_checkmate"] is False
    assert actual["is_castling"] is False
    assert actual["before"]["side_to_move"] == "white"
    assert actual["before"]["legal_move_count"] == 20
    assert actual["before"]["material"] == {
        "white_points": 39,
        "black_points": 39,
        "white_pieces": 15,
        "black_pieces": 15,
    }
    assert actual["after"]["side_to_move"] == "black"
    assert actual["after"]["legal_move_count"] == 20
    assert actual["terminal"] is None
    assert actual["material_change"] == {
        "white_points": 0,
        "black_points": 0,
        "white_pieces": 0,
        "black_pieces": 0,
    }


def test_analysis_position_reports_capture_forced_and_terminal_facts(
    client: TestClient,
) -> None:
    response = _analysis_post(
        client,
        "/tools/analysis/position",
        {"fen": _FORCED_CAPTURE_FEN, "move": "Kxb2"},
    )

    assert response.status_code == 200
    actual = response.json()["actual_move"]
    assert actual["san"] == "Kxb2"
    assert actual["uci"] == "a1b2"
    assert actual["captured_piece"] == "queen"
    assert actual["captured_color"] == "black"
    assert actual["is_capture"] is True
    assert actual["before"]["legal_move_count"] == 1
    assert actual["material_change"] == {
        "white_points": 0,
        "black_points": -9,
        "white_pieces": 0,
        "black_pieces": -1,
    }
    assert actual["terminal"] == {
        "kind": "insufficient_material",
        "winner": None,
    }


def test_analysis_position_reports_castling_promotion_and_check(
    client: TestClient,
) -> None:
    castling = _analysis_post(
        client,
        "/tools/analysis/position",
        {"fen": _CASTLE_FEN, "move": "O-O"},
    ).json()["actual_move"]
    promotion = _analysis_post(
        client,
        "/tools/analysis/position",
        {"fen": _PROMOTION_FEN, "move": "a7a8q"},
    ).json()["actual_move"]
    check = _analysis_post(
        client,
        "/tools/analysis/position",
        {"fen": _CHECK_FEN, "move": "Ra8+"},
    ).json()["actual_move"]

    assert castling["is_castling"] is True
    assert castling["before"]["castling_rights"] == "KQkq"
    assert castling["after"]["castling_rights"] == "kq"
    assert promotion["promotion"] == "queen"
    assert promotion["after"]["material"]["white_points"] == 9
    assert promotion["after"]["material"]["white_pieces"] == 1
    assert promotion["material_change"] == {
        "white_points": 8,
        "black_points": 0,
        "white_pieces": 0,
        "black_pieces": 0,
    }
    assert check["is_check"] is True
    assert check["after"]["check"] is True


@pytest.mark.parametrize(
    "move",
    ["e5", "e2e5", "not-a-move", "0000", "--", "Z0", "Nf3#", "Nf3+"],
)
def test_analysis_position_rejects_an_invalid_actual_move(
    client: TestClient, move: str
) -> None:
    response = _analysis_post(
        client,
        "/tools/analysis/position",
        {"fen": _START_FEN, "move": move},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_move"


def test_analysis_position_reports_an_en_passant_capture(
    client: TestClient,
) -> None:
    en_passant_fen = (
        "rnbqkbnr/ppp1pppp/8/3pP3/8/8/PPPP1PPP/RNBQKBNR w KQkq d6 0 3"
    )
    response = _analysis_post(
        client,
        "/tools/analysis/position",
        {"fen": en_passant_fen, "move": "exd6"},
    )

    assert response.status_code == 200
    actual = response.json()["actual_move"]
    assert actual["is_capture"] is True
    assert actual["captured_piece"] == "pawn"
    assert actual["captured_color"] == "black"
    assert actual["after"]["material"]["black_points"] == 38


def test_analysis_position_accepts_san_with_an_omitted_check_marker(
    client: TestClient,
) -> None:
    response = _analysis_post(
        client,
        "/tools/analysis/position",
        {"fen": _CHECK_FEN, "move": "Ra8"},
    )

    assert response.status_code == 200
    assert response.json()["actual_move"]["san"] == "Ra8+"


def test_analysis_position_preserves_the_side_to_move(client: TestClient) -> None:
    response = _analysis_post(
        client, "/tools/analysis/position", {"fen": _BLACK_TO_MOVE_FEN}
    )

    assert response.status_code == 200
    assert response.json()["side_to_move"] == "black"


def test_analysis_position_reports_checkmate_without_candidate_lines(
    client: TestClient,
) -> None:
    response = _analysis_post(
        client, "/tools/analysis/position", {"fen": _CHECKMATE_FEN}
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["terminal"] == {"kind": "checkmate", "winner": "black"}
    assert payload["candidate_lines"] == []


@pytest.mark.parametrize(
    ("fen", "terminal"),
    [
        (_STALEMATE_FEN, {"kind": "stalemate", "winner": None}),
        (
            _INSUFFICIENT_FEN,
            {"kind": "insufficient_material", "winner": None},
        ),
        (
            _SEVENTYFIVE_MOVE_FEN,
            {"kind": "seventyfive_moves", "winner": None},
        ),
    ],
)
def test_analysis_position_reports_draws_without_candidate_lines(
    client: TestClient, fen: str, terminal: dict[str, object]
) -> None:
    response = _analysis_post(
        client, "/tools/analysis/position", {"fen": fen}
    )

    assert response.status_code == 200
    assert response.json()["terminal"] == terminal
    assert response.json()["candidate_lines"] == []


def test_analysis_lines_are_ranked_san_from_white_perspective(
    client: TestClient,
) -> None:
    response = _analysis_post(
        client,
        "/tools/analysis/lines",
        {
            "fen": _START_FEN,
            "lines": [
                {
                    "multipv": 1,
                    "depth": 18,
                    "score": {"kind": "cp", "value": 35},
                    "pv": ["e2e4", "e7e5", "g1f3"],
                },
                {
                    "multipv": 2,
                    "depth": 17,
                    "score": {"kind": "cp", "value": -20},
                    "pv": ["d2d4", "d7d5"],
                },
            ],
        },
    )

    assert response.status_code == 200
    lines = response.json()["lines"]
    assert [line["rank"] for line in lines] == [1, 2]
    assert lines[0]["evaluation"] == {
        "kind": "cp",
        "value": 35,
        "text": "+0.35",
    }
    assert lines[0]["depth"] == 18
    assert [move["san"] for move in lines[0]["moves"]] == ["e4", "e5", "Nf3"]
    assert [move["uci"] for move in lines[0]["moves"]] == [
        "e2e4",
        "e7e5",
        "g1f3",
    ]
    assert [move["fen"] for move in lines[0]["moves"]] == [
        _AFTER_E4_FEN,
        "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2",
        "rnbqkbnr/pppp1ppp/8/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R b KQkq - 1 2",
    ]
    assert lines[1]["evaluation"]["text"] == "-0.20"
    assert [move["san"] for move in lines[1]["moves"]] == ["d4", "d5"]


def test_analysis_lines_normalize_black_scores_and_mate(
    client: TestClient,
) -> None:
    black = _analysis_post(
        client,
        "/tools/analysis/lines",
        {
            "fen": _BLACK_TO_MOVE_FEN,
            "lines": [
                {
                    "multipv": 1,
                    "depth": 20,
                    "score": {"kind": "cp", "value": 50},
                    "pv": ["e7e5", "g1f3"],
                }
            ],
        },
    )
    mate = _analysis_post(
        client,
        "/tools/analysis/lines",
        {
            "fen": _START_FEN,
            "lines": [
                {
                    "multipv": 1,
                    "depth": 22,
                    "score": {"kind": "mate", "value": 3},
                    "pv": ["e2e4"],
                }
            ],
        },
    )
    black_mate = _analysis_post(
        client,
        "/tools/analysis/lines",
        {
            "fen": _BLACK_TO_MOVE_FEN,
            "lines": [
                {
                    "multipv": 1,
                    "depth": 22,
                    "score": {"kind": "mate", "value": 4},
                    "pv": ["e7e5"],
                }
            ],
        },
    )

    assert black.status_code == 200
    assert black.json()["lines"][0]["evaluation"] == {
        "kind": "cp",
        "value": -50,
        "text": "-0.50",
    }
    assert [move["san"] for move in black.json()["lines"][0]["moves"]] == [
        "e5",
        "Nf3",
    ]
    assert mate.status_code == 200
    assert mate.json()["lines"][0]["evaluation"] == {
        "kind": "mate",
        "value": 3,
        "text": "+M3",
    }
    assert black_mate.status_code == 200
    assert black_mate.json()["lines"][0]["evaluation"] == {
        "kind": "mate",
        "value": -4,
        "text": "-M4",
    }


def test_analysis_engine_assets_are_self_hosted_with_provenance(
    client: TestClient,
) -> None:
    engine_js = client.get(
        "/static/vendor/stockfish/stockfish-19-lite-single.js"
    )
    engine_wasm = client.get(
        "/static/vendor/stockfish/stockfish-19-lite-single.wasm"
    )
    license_file = client.get(
        "/static/vendor/stockfish/Copying.txt"
    )
    provenance = client.get(
        "/static/vendor/stockfish/PROVENANCE.txt"
    )
    full_js = client.get(
        "/static/vendor/stockfish/stockfish-19-single.js"
    )
    full_wasm = client.head(
        "/static/vendor/stockfish/stockfish-19-single.wasm"
    )
    multi_js = client.get(
        "/static/vendor/stockfish/stockfish-19-lite-multi.js"
    )
    multi_wasm = client.head(
        "/static/vendor/stockfish/stockfish-19-lite-multi.wasm"
    )

    assert engine_js.status_code == 200
    assert engine_js.headers["content-type"].split(";")[0] in {
        "text/javascript",
        "application/javascript",
    }
    assert len(engine_js.content) > 10_000
    assert engine_wasm.status_code == 200
    assert engine_wasm.headers["content-type"].split(";")[0] == "application/wasm"
    assert engine_wasm.content.startswith(b"\x00asm")
    assert license_file.status_code == 200
    assert license_file.headers["content-type"].startswith("text/plain")
    assert "GNU GENERAL PUBLIC LICENSE" in license_file.text
    assert provenance.status_code == 200
    assert "stockfish.js 19.0.1" in provenance.text
    assert "https://github.com/nmrugg/stockfish.js" in provenance.text
    assert "stockfish-19-single.wasm" in provenance.text
    assert "stockfish-19-lite-multi.wasm" in provenance.text
    for response in (engine_js, engine_wasm, multi_js, multi_wasm):
        assert response.headers["cross-origin-resource-policy"] == "same-origin"
        assert (
            response.headers["cross-origin-embedder-policy"] == "require-corp"
        )
    assert multi_js.status_code == 200
    assert multi_js.headers["content-type"].split(";")[0] in {
        "text/javascript",
        "application/javascript",
    }
    assert multi_wasm.status_code == 200
    assert multi_wasm.headers["content-type"].split(";")[0] == "application/wasm"
    assert full_js.status_code == 200
    assert full_js.headers["content-type"].split(";")[0] in {
        "text/javascript",
        "application/javascript",
    }
    assert full_wasm.status_code == 200
    assert full_wasm.headers["content-type"].split(";")[0] == "application/wasm"


@pytest.mark.parametrize(
    ("after_value", "expected_cpl", "expected_quality"),
    [
        (-90, 10, "best"),
        (-89, 11, "excellent"),
        (-70, 30, "excellent"),
        (-49, 51, "good"),
        (-29, 71, "inaccuracy"),
        (50, 150, "inaccuracy"),
        (200, 300, "mistake"),
        (201, 301, "blunder"),
    ],
)
def test_analysis_assessment_uses_the_specified_cpl_bands(
    client: TestClient,
    after_value: int,
    expected_cpl: int,
    expected_quality: str,
) -> None:
    response = _analysis_post(
        client,
        "/tools/analysis/assess",
        {
            "fen": _START_FEN,
            "move": "e2e4",
            "lines": [
                {
                    "multipv": 1,
                    "depth": 18,
                    "score": {"kind": "cp", "value": 100},
                    "pv": ["d2d4", "d7d5"],
                }
            ],
            "after_lines": [
                {
                    "multipv": 1,
                    "depth": 18,
                    "score": {"kind": "cp", "value": after_value},
                    "pv": ["e7e5"],
                }
            ],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assessment = payload["assessment"]
    assert assessment["cpl"] == expected_cpl
    assert assessment["mate_loss"] is False
    assert assessment["quality"] == expected_quality
    assert assessment["best_move"]["san"] == "d4"
    assert assessment["actual_move"]["san"] == "e4"
    assert assessment["best_evaluation"] == {
        "kind": "cp",
        "value": 100,
        "text": "+1.00",
    }
    assert assessment["actual_evaluation"]["value"] == -after_value
    assert assessment["actual_move"]["fen"] == _AFTER_E4_FEN
    assert assessment["terminal"] is None
    assert assessment["is_forced"] is False


def test_analysis_assessment_uses_the_black_movers_perspective(
    client: TestClient,
) -> None:
    response = _analysis_post(
        client,
        "/tools/analysis/assess",
        {
            "fen": _BLACK_TO_MOVE_FEN,
            "move": "e7e5",
            "lines": [
                {
                    "multipv": 1,
                    "depth": 18,
                    "score": {"kind": "cp", "value": 100},
                    "pv": ["e7e5", "g1f3"],
                }
            ],
            "after_lines": [
                {
                    "multipv": 1,
                    "depth": 18,
                    "score": {"kind": "cp", "value": 150},
                    "pv": ["g1f3"],
                }
            ],
        },
    )

    assert response.status_code == 200
    assessment = response.json()["assessment"]
    assert assessment["cpl"] == 250
    assert assessment["quality"] == "mistake"


def test_analysis_assessment_requires_after_lines_for_a_nonterminal_move(
    client: TestClient,
) -> None:
    response = _analysis_post(
        client,
        "/tools/analysis/assess",
        {
            "fen": _START_FEN,
            "move": "e2e4",
            "lines": [
                {
                    "multipv": 1,
                    "depth": 18,
                    "score": {"kind": "cp", "value": 100},
                    "pv": ["e2e4"],
                }
            ],
            "after_lines": [],
        },
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_candidate_line"


def test_analysis_assessment_marks_a_forced_move_and_draw(
    client: TestClient,
) -> None:
    response = _analysis_post(
        client,
        "/tools/analysis/assess",
        {
            "fen": _FORCED_CAPTURE_FEN,
            "move": "a1b2",
            "lines": [
                {
                    "multipv": 1,
                    "depth": 18,
                    "score": {"kind": "cp", "value": 0},
                    "pv": ["a1b2"],
                }
            ],
            "after_lines": [],
        },
    )

    assert response.status_code == 200
    assessment = response.json()["assessment"]
    assert assessment["quality"] == "forced"
    assert assessment["is_forced"] is True
    assert assessment["cpl"] == 0
    assert assessment["mate_loss"] is False
    assert assessment["actual_evaluation"] == {
        "kind": "cp",
        "value": 0,
        "text": "0.00",
    }
    assert assessment["terminal"] == {
        "kind": "insufficient_material",
        "winner": None,
    }


def test_analysis_assessment_is_mate_aware(
    client: TestClient,
) -> None:
    lost_mate = _analysis_post(
        client,
        "/tools/analysis/assess",
        {
            "fen": _START_FEN,
            "move": "e2e4",
            "lines": [
                {
                    "multipv": 1,
                    "depth": 20,
                    "score": {"kind": "cp", "value": 0},
                    "pv": ["d2d4"],
                }
            ],
            "after_lines": [
                {
                    "multipv": 1,
                    "depth": 20,
                    "score": {"kind": "mate", "value": 2},
                    "pv": ["e7e5"],
                }
            ],
        },
    )
    missed_mate = _analysis_post(
        client,
        "/tools/analysis/assess",
        {
            "fen": _START_FEN,
            "move": "e2e4",
            "lines": [
                {
                    "multipv": 1,
                    "depth": 20,
                    "score": {"kind": "mate", "value": 3},
                    "pv": ["d2d4"],
                }
            ],
            "after_lines": [
                {
                    "multipv": 1,
                    "depth": 20,
                    "score": {"kind": "cp", "value": 0},
                    "pv": ["e7e5"],
                }
            ],
        },
    )

    assert lost_mate.status_code == 200
    lost = lost_mate.json()["assessment"]
    assert lost["actual_evaluation"]["text"] == "-M2"
    assert lost["quality"] == "blunder"
    assert lost["cpl"] is None
    assert lost["mate_loss"] is True

    assert missed_mate.status_code == 200
    missed = missed_mate.json()["assessment"]
    assert missed["best_evaluation"]["text"] == "+M3"
    assert missed["quality"] == "blunder"
    assert missed["cpl"] is None
    assert missed["mate_loss"] is True


def test_analysis_assessment_recognizes_a_checkmating_move(
    client: TestClient,
) -> None:
    fen = "rnbqkbnr/pppp1ppp/8/4p3/6P1/5P2/PPPPP2P/RNBQKBNR b KQkq g3 0 2"
    response = _analysis_post(
        client,
        "/tools/analysis/assess",
        {
            "fen": fen,
            "move": "d8h4",
            "lines": [
                {
                    "multipv": 1,
                    "depth": 18,
                    "score": {"kind": "cp", "value": 500},
                    "pv": ["d8h4"],
                }
            ],
            "after_lines": [],
        },
    )

    assert response.status_code == 200
    assessment = response.json()["assessment"]
    assert assessment["quality"] == "best"
    assert assessment["cpl"] == 0
    assert assessment["mate_loss"] is False
    assert assessment["actual_evaluation"] is None
    assert assessment["terminal"] == {
        "kind": "checkmate",
        "winner": "black",
    }


def test_analysis_assessment_rejects_an_illegal_move(
    client: TestClient,
) -> None:
    response = _analysis_post(
        client,
        "/tools/analysis/assess",
        {
            "fen": _START_FEN,
            "move": "e2e5",
            "lines": [
                {
                    "multipv": 1,
                    "depth": 18,
                    "score": {"kind": "cp", "value": 0},
                    "pv": ["e2e4"],
                }
            ],
            "after_lines": [],
        },
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_move"


def test_analysis_lines_reject_unbounded_scores(client: TestClient) -> None:
    response = _analysis_post(
        client,
        "/tools/analysis/lines",
        {
            "fen": _START_FEN,
            "lines": [
                {
                    "multipv": 1,
                    "depth": 18,
                    "score": {"kind": "cp", "value": 10**400},
                    "pv": ["e2e4"],
                }
            ],
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"


def test_analysis_lines_reject_illegal_principal_variations(
    client: TestClient,
) -> None:
    first_move = _analysis_post(
        client,
        "/tools/analysis/lines",
        {
            "fen": _START_FEN,
            "lines": [
                {
                    "multipv": 1,
                    "depth": 18,
                    "score": {"kind": "cp", "value": 20},
                    "pv": ["e2e5"],
                }
            ],
        },
    )
    later_move = _analysis_post(
        client,
        "/tools/analysis/lines",
        {
            "fen": _START_FEN,
            "lines": [
                {
                    "multipv": 1,
                    "depth": 18,
                    "score": {"kind": "cp", "value": 20},
                    "pv": ["e2e4", "g8e5"],
                }
            ],
        },
    )

    assert first_move.status_code == 400
    assert first_move.json()["error"]["code"] == "invalid_candidate_line"
    assert later_move.status_code == 400
    assert later_move.json()["error"]["code"] == "invalid_candidate_line"


def test_analysis_lines_reject_duplicate_ranks(client: TestClient) -> None:
    response = _analysis_post(
        client,
        "/tools/analysis/lines",
        {
            "fen": _START_FEN,
            "lines": [
                {
                    "multipv": 1,
                    "depth": 18,
                    "score": {"kind": "cp", "value": 20},
                    "pv": ["e2e4"],
                },
                {
                    "multipv": 1,
                    "depth": 18,
                    "score": {"kind": "cp", "value": 10},
                    "pv": ["d2d4"],
                },
            ],
        },
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_candidate_line"


_PGN_TWO_GAMES = '''[Event "One"]
[White "Alice"]
[Black "Bob"]
[Result "1-0"]

1. e4 e5 2. Nf3 Nc6 1-0

[Event "Two"]
[White "Carol"]
[Black "Dave"]
[Result "0-1"]

1. d4 d5 0-1
'''

_PGN_ONE_MOVE = '''[Event "Short"]
[White "Alice"]
[Black "Bob"]
[Result "*"]

1. e4 *
'''


def test_game_analysis_page_parses_and_opens_a_selected_game(
    client: TestClient,
) -> None:
    parsed = client.post(
        "/tools/analysis/pgn",
        data={"pgn": _PGN_TWO_GAMES, "mode": "parse"},
        headers={**_EN, **_ORIGIN},
    )
    opened = client.post(
        "/tools/analysis/pgn",
        data={"pgn": _PGN_TWO_GAMES, "game_index": "1", "mode": "open"},
        headers={**_EN, **_ORIGIN},
    )

    assert parsed.status_code == 200
    assert "Alice vs Bob" in parsed.text
    assert "Carol vs Dave" in parsed.text
    assert opened.status_code == 200
    assert "data-game-root" in opened.text
    assert '"total_plies": 2' in opened.text
    assert '"d2d4"' in opened.text or '"d4"' in opened.text


def test_game_analysis_page_rejects_invalid_pgn_and_long_games(
    client: TestClient,
) -> None:
    invalid = client.post(
        "/tools/analysis/pgn",
        data={"pgn": "not a pgn", "mode": "parse"},
        headers={**_EN, **_ORIGIN},
    )

    assert invalid.status_code == 400
    assert "Invalid PGN" in invalid.text


def test_game_results_compute_n_plus_one_evaluations_and_key_plies(
    client: TestClient,
) -> None:
    response = _analysis_post(
        client,
        "/tools/analysis/game/results",
        {
            "start_fen": _START_FEN,
            "moves": ["e2e4"],
            "positions": [
                {
                    "lines": [
                        {
                            "multipv": 1,
                            "depth": 18,
                            "score": {"kind": "cp", "value": 20},
                            "pv": ["e2e4", "e7e5"],
                        },
                        {
                            "multipv": 2,
                            "depth": 18,
                            "score": {"kind": "cp", "value": -130},
                            "pv": ["d2d4", "d7d5"],
                        },
                    ]
                },
                {
                    "lines": [
                        {
                            "multipv": 1,
                            "depth": 18,
                            "score": {"kind": "cp", "value": -10},
                            "pv": ["e7e5", "g1f3"],
                        }
                    ]
                },
            ],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["evaluations"]) == 2
    assert len(payload["plies"]) == 1
    ply = payload["plies"][0]
    assert ply["ply"] == 1
    assert ply["san"] == "e4"
    assert ply["quality"] == "best"
    assert ply["is_key"] is True
    assert "only_best" in ply["key_reasons"]
    assert ply["exact_facts"]["is_capture"] is False
    assert "doubled_pawns" in ply["indicators"]
    assert payload["key_plies"] == [1]


def test_game_analysis_page_rejects_more_than_400_plies(
    client: TestClient,
) -> None:
    game = chess.pgn.Game()
    game.headers["Event"] = "Long"
    game.headers["White"] = "Alice"
    game.headers["Black"] = "Bob"
    game.headers["Result"] = "*"
    node: chess.pgn.GameNode = game
    for _ in range(101):
        for uci in ("g1f3", "g8f6", "f3g1", "f6g8"):
            node = node.add_variation(chess.Move.from_uci(uci))
    pgn = game.accept(chess.pgn.StringExporter(headers=True, comments=False))

    response = client.post(
        "/tools/analysis/pgn",
        data={"pgn": pgn, "game_index": "0", "mode": "open"},
        headers={**_EN, **_ORIGIN},
    )

    assert response.status_code == 400
    assert "Analysis supports Games up to 400 Ply." in response.text


def test_settings_page_exposes_secure_llm_profile_controls(
    client: TestClient,
) -> None:
    page = client.get("/settings", headers=_EN)

    assert page.status_code == 200
    assert "LLM Profile" in page.text
    assert 'data-llm-api-key' in page.text
    assert 'data-llm-passphrase' in page.text
    assert 'data-llm-provider' in page.text
    assert "Reasoning effort" in page.text
    assert 'value="16384"' in page.text
    assert page.headers["content-security-policy"].find(
        "connect-src 'self' https:"
    ) >= 0


def test_settings_and_export_controls_are_localized(
    client: TestClient,
) -> None:
    expected = {
        "zh": {
            "settings": ["分析设置", "Kimi 区域", "思考方言"],
            "analysis": ["导出分析", "导出注释 PGN", "导出 Markdown 报告"],
        },
        "de": {
            "settings": [
                "Analyseeinstellungen",
                "Kimi-Region",
                "Reasoning-Dialekt",
            ],
            "analysis": [
                "Analyse exportieren",
                "Annotiertes PGN exportieren",
                "Markdown-Bericht exportieren",
            ],
        },
    }
    for language, strings in expected.items():
        headers = {"Accept-Language": language}
        settings = client.get("/settings", headers=headers)
        analysis = client.get("/tools/analysis", headers=headers)
        game = client.get("/tools/analysis/pgn", headers=headers)
        assert settings.status_code == 200
        assert analysis.status_code == 200
        assert game.status_code == 200
        for text in strings["settings"]:
            assert text in settings.text
        for text in strings["analysis"]:
            assert text in analysis.text
            assert text in game.text
