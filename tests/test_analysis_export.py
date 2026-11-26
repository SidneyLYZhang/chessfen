from __future__ import annotations

import io

import chess
import chess.pgn
import pytest
from fastapi.testclient import TestClient

from chessfen.analysis_export import (
    CandidateExport,
    ExportError,
    ExportExplanation,
    PlyAnnotation,
    annotated_game_pgn,
    position_pgn,
)

_EN = {"Accept-Language": "en"}
_ORIGIN = {"Origin": "http://testserver"}
_START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
_PGN = """[Event "Original"]
[Site "Local"]
[White "Alice"]
[Black "Bob"]
[Result "1-0"]

1. e4 {Original first move} (1. d4 d5) 1... e5 $1 2. Nf3 {Original knight} Nc6
3. Bb5 a6 1-0
"""
_SECOND_PGN = """[Event "Second"]
[White "Carol"]
[Black "Dan"]
[Result "0-1"]

1. d4 d5 0-1
"""
_EXPLANATION = ExportExplanation(
    summary="Central pressure.",
    why="Candidate Lines support the centre.",
    alternatives="The second line is playable.",
    uncertainty="Strategic intent is limited.",
)


def _read_game(content: str) -> chess.pgn.Game:
    game = chess.pgn.read_game(io.StringIO(content))
    assert game is not None
    return game


def test_game_export_preserves_source_and_attaches_annotations() -> None:
    content = annotated_game_pgn(
        _PGN,
        0,
        [
            PlyAnnotation(
                ply=1,
                quality="best",
                score_kind="cp",
                score_value=20,
                cpl=0,
                mate_loss=False,
                explanation=_EXPLANATION,
            ),
            PlyAnnotation(
                ply=2,
                quality="inaccuracy",
                score_kind="cp",
                score_value=-80,
                cpl=180,
                mate_loss=False,
            ),
        ],
        include_explanations=True,
    )
    game = _read_game(content)
    assert game.headers["Event"] == "Original"
    assert game.headers["White"] == "Alice"
    assert len(game.variations) == 2
    assert game.variations[1].san() == "d4"
    first = game.next()
    assert first is not None
    assert first.san() == "e4"
    assert "Original first move" in (first.comment or "")
    assert "[%eval 0.20]" in (first.comment or "")
    assert "Quality: Best." in (first.comment or "")
    assert "Central pressure." in (first.comment or "")
    assert 1 in first.nags
    second = first.next()
    assert second is not None
    assert second.san() == "e5"
    assert "[%eval -0.80]" in (second.comment or "")
    assert "[%cpl 180]" in (second.comment or "")
    assert "Quality: Inaccuracy." in (second.comment or "")
    assert 1 in second.nags
    assert 6 in second.nags
    assert list(game.mainline_moves())[:3] == [
        chess.Move.from_uci("e2e4"),
        chess.Move.from_uci("e7e5"),
        chess.Move.from_uci("g1f3"),
    ]
    third = second.next()
    assert third is not None
    assert "Original knight" in (third.comment or "")


def test_game_export_uses_standard_nags_for_each_quality() -> None:
    content = annotated_game_pgn(
        _PGN,
        0,
        [
            PlyAnnotation(1, "best", "cp", 20, 0, False),
            PlyAnnotation(2, "inaccuracy", "cp", -80, 180, False),
            PlyAnnotation(3, "mistake", "cp", -200, 280, False),
            PlyAnnotation(4, "blunder", "cp", -500, 600, False),
            PlyAnnotation(5, "good", "cp", 0, 50, False),
            PlyAnnotation(6, "forced", "cp", 0, 0, False),
        ],
        include_explanations=False,
    )
    game = _read_game(content)
    node = game.next()
    assert node is not None
    expected = [
        {1},
        {1, 6},
        {2},
        {4},
        set(),
        set(),
    ]
    for nags in expected:
        assert node.nags == nags
        following = node.next()
        if following is None:
            break
        node = following


def test_game_export_selects_one_game_and_can_omit_explanations() -> None:
    pgn = _PGN + "\n" + _SECOND_PGN
    content = annotated_game_pgn(
        pgn,
        1,
        [
            PlyAnnotation(
                ply=1,
                quality="good",
                score_kind="cp",
                score_value=10,
                cpl=20,
                mate_loss=False,
                explanation=_EXPLANATION,
            )
        ],
        include_explanations=False,
    )
    game = _read_game(content)
    assert game.headers["Event"] == "Second"
    assert game.headers["White"] == "Carol"
    assert "Central pressure." not in content
    assert "Quality: Good." in content
    assert "Alice" not in content


def test_game_export_rejects_invalid_ply_and_comment_braces() -> None:
    with pytest.raises(ExportError):
        annotated_game_pgn(
            _PGN,
            0,
            [
                PlyAnnotation(
                    ply=99,
                    quality="best",
                    score_kind="cp",
                    score_value=0,
                    cpl=0,
                    mate_loss=False,
                )
            ],
            include_explanations=True,
        )
    content = annotated_game_pgn(
        _PGN,
        0,
        [
            PlyAnnotation(
                ply=1,
                quality="best",
                score_kind="cp",
                score_value=0,
                cpl=0,
                mate_loss=False,
                explanation=ExportExplanation(
                    summary="braces } here",
                    why="why",
                    alternatives="alt",
                    uncertainty="unknown",
                ),
            )
        ],
        include_explanations=True,
    )
    assert "braces } here" not in content
    assert "braces ) here" in content


def test_position_export_uses_fen_setup_and_legal_variations() -> None:
    content = position_pgn(
        _START_FEN,
        [
            CandidateExport(
                rank=1,
                score_kind="cp",
                score_value=30,
                moves=("e2e4", "e7e5"),
            ),
            CandidateExport(
                rank=2,
                score_kind="cp",
                score_value=20,
                moves=("d2d4", "d7d5"),
            ),
        ],
        "Position summary.",
        include_explanations=True,
    )
    game = _read_game(content)
    assert game.headers["SetUp"] == "1"
    assert game.headers["FEN"] == _START_FEN
    assert game.board().fen() == _START_FEN
    first = game.next()
    assert first is not None
    assert first.san() == "e4"
    assert len(game.variations) == 2
    assert game.variations[1].san() == "d4"
    assert "Position summary." in (game.comment or "")
    assert "#1 0.30" in (first.comment or "")


def test_position_export_rejects_illegal_candidate_line() -> None:
    with pytest.raises(ExportError):
        position_pgn(
            _START_FEN,
            [
                CandidateExport(
                    rank=1,
                    score_kind="cp",
                    score_value=0,
                    moves=("e2e5",),
                )
            ],
            None,
            include_explanations=False,
        )


def test_export_routes_require_origin_and_forbid_extra_fields(
    client: TestClient,
) -> None:
    payload = {
        "pgn": _PGN,
        "game_index": 0,
        "include_explanations": False,
        "annotations": [
            {
                "ply": 1,
                "quality": "best",
                "score_kind": "cp",
                "score_value": 20,
                "cpl": 0,
                "mate_loss": False,
            }
        ],
    }
    denied = client.post("/tools/analysis/export/game/pgn", json=payload)
    assert denied.status_code == 403
    assert denied.json() == {"error": {"code": "invalid_origin"}}
    response = client.post(
        "/tools/analysis/export/game/pgn",
        json=payload,
        headers={**_EN, **_ORIGIN},
    )
    assert response.status_code == 200
    content = response.json()["content"]
    assert "Quality: Best." in content
    secret = {**payload, "api_key": "sk-secret"}
    rejected = client.post(
        "/tools/analysis/export/game/pgn",
        json=secret,
        headers={**_EN, **_ORIGIN},
    )
    assert rejected.status_code == 422
    assert "sk-secret" not in rejected.text
