from __future__ import annotations

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
    assert page.text.count("<script") == 2
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
    assert "cross-origin-opener-policy" not in page.headers

    zh = client.get("/tools/analysis", headers=_ZH)
    de = client.get("/tools/analysis", headers=_DE)
    assert "仅棋盘布局不足以进行分析。" in zh.text
    assert "Nur die Brettaufstellung reicht für eine Analyse nicht aus." in de.text
    assert "请使用包含六个字段的 Full FEN。" in zh.text
    assert "Verwende eine Full FEN mit sechs Feldern." in de.text
    assert 'aria-label="引擎详情"' in zh.text
    assert 'aria-label="Engine-Details"' in de.text


def test_existing_fen_and_pgn_tools_link_to_analysis(
    client: TestClient,
) -> None:
    for path in ("/tools/render", "/tools/pgn"):
        page = client.get(path, headers=_EN)

        assert page.status_code == 200, path
        assert ">Open Analysis" in page.text, path
        assert 'href="/tools/analysis"' in page.text, path


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
