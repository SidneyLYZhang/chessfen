from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias

import chess

from chessfen.errors import CodedError
from chessfen.render import RenderOptions, data_uri, render_fen

BOARD_SIZE = 560

ScoreKind: TypeAlias = Literal["cp", "mate"]


class AnalysisInputError(CodedError):
    """A Position or engine result cannot be used for Position Analysis."""


@dataclass(frozen=True)
class TerminalResult:
    kind: str
    winner: str | None = None


@dataclass(frozen=True)
class PositionAnalysis:
    fen: str
    side_to_move: str
    board_uri: str
    terminal: TerminalResult | None


@dataclass(frozen=True)
class CandidateInput:
    multipv: int
    depth: int
    score_kind: ScoreKind
    score_value: int
    pv: tuple[str, ...]


@dataclass(frozen=True)
class Evaluation:
    kind: ScoreKind
    value: int
    text: str


@dataclass(frozen=True)
class LineMove:
    uci: str
    san: str


@dataclass(frozen=True)
class CandidateLine:
    rank: int
    depth: int
    evaluation: Evaluation
    moves: tuple[LineMove, ...]


def parse_analysis_fen(value: str) -> chess.Board:
    text = value.strip()
    fields = text.split()
    if len(fields) == 1:
        raise AnalysisInputError("placement_only")
    if len(fields) != 6:
        raise AnalysisInputError("invalid_fen")
    try:
        board = chess.Board(text)
    except ValueError as exc:
        raise AnalysisInputError("invalid_fen") from exc
    if not board.is_valid():
        raise AnalysisInputError("illegal_position")
    return board


def terminal_result(board: chess.Board) -> TerminalResult | None:
    if board.is_checkmate():
        return TerminalResult(
            kind="checkmate", winner="white" if board.turn == chess.BLACK else "black"
        )
    if board.is_stalemate():
        return TerminalResult(kind="stalemate")
    if board.is_insufficient_material():
        return TerminalResult(kind="insufficient_material")
    if board.is_seventyfive_moves():
        return TerminalResult(kind="seventyfive_moves")
    return None


def _white_perspective(
    white_to_move: bool, score_kind: ScoreKind, score_value: int
) -> Evaluation:
    value = score_value if white_to_move else -score_value
    if score_kind == "cp":
        if value == 0:
            text = "0.00"
        else:
            text = f"{value / 100:+.2f}"
        return Evaluation(kind="cp", value=value, text=text)
    if value == 0:
        text = "M0"
    elif value > 0:
        text = f"+M{value}"
    else:
        text = f"-M{abs(value)}"
    return Evaluation(kind="mate", value=value, text=text)


def candidate_lines(
    fen: str, candidates: list[CandidateInput]
) -> tuple[CandidateLine, ...]:
    root = parse_analysis_fen(fen)
    if len({item.multipv for item in candidates}) != len(candidates):
        raise AnalysisInputError("invalid_candidate_line")
    lines: list[CandidateLine] = []
    for candidate in sorted(candidates, key=lambda item: item.multipv):
        board = root.copy(stack=False)
        moves: list[LineMove] = []
        try:
            for uci in candidate.pv:
                move = chess.Move.from_uci(uci)
                if move not in board.legal_moves:
                    raise ValueError(uci)
                san = board.san(move)
                moves.append(LineMove(uci=uci, san=san))
                board.push(move)
        except ValueError as exc:
            raise AnalysisInputError("invalid_candidate_line") from exc
        if not moves:
            raise AnalysisInputError("invalid_candidate_line")
        lines.append(
            CandidateLine(
                rank=candidate.multipv,
                depth=candidate.depth,
                evaluation=_white_perspective(
                    root.turn == chess.WHITE,
                    candidate.score_kind,
                    candidate.score_value,
                ),
                moves=tuple(moves),
            )
        )
    return tuple(lines)


def analyze_position(value: str) -> PositionAnalysis:
    board = parse_analysis_fen(value)
    rendered = render_fen(
        board.fen(),
        RenderOptions(
            size=BOARD_SIZE,
            coordinates=True,
            output_format="svg",
        ),
    )
    board_uri = data_uri(rendered.media_type, rendered.image_bytes)
    return PositionAnalysis(
        fen=board.fen(),
        side_to_move="white" if board.turn == chess.WHITE else "black",
        board_uri=board_uri,
        terminal=terminal_result(board),
    )
