from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, TypeAlias

import chess

from chessfen.errors import CodedError
from chessfen.render import RenderOptions, data_uri, render_fen

BOARD_SIZE = 560

ScoreKind: TypeAlias = Literal["cp", "mate"]
MoveQuality: TypeAlias = Literal[
    "best",
    "excellent",
    "good",
    "inaccuracy",
    "mistake",
    "blunder",
    "forced",
]

_MATE_SCORE = 100_000
_MATE_CPL = 1000

_PIECE_ROLES = {
    chess.PAWN: "pawn",
    chess.KNIGHT: "knight",
    chess.BISHOP: "bishop",
    chess.ROOK: "rook",
    chess.QUEEN: "queen",
    chess.KING: "king",
}
_MATERIAL_POINTS = {
    chess.PAWN: 1,
    chess.KNIGHT: 3,
    chess.BISHOP: 3,
    chess.ROOK: 5,
    chess.QUEEN: 9,
}
_UCI_RE = re.compile(r"^[a-h][1-8][a-h][1-8][qrbn]?$", re.IGNORECASE)


class AnalysisInputError(CodedError):
    """A Position or engine result cannot be used for Position Analysis."""


@dataclass(frozen=True)
class TerminalResult:
    kind: str
    winner: str | None = None


@dataclass(frozen=True)
class MaterialSummary:
    white_points: int
    black_points: int
    white_pieces: int
    black_pieces: int


@dataclass(frozen=True)
class MaterialChange:
    white_points: int
    black_points: int
    white_pieces: int
    black_pieces: int


@dataclass(frozen=True)
class PositionFacts:
    fen: str
    side_to_move: str
    legal_move_count: int
    check: bool
    castling_rights: str
    material: MaterialSummary


@dataclass(frozen=True)
class ActualMoveFacts:
    uci: str
    san: str
    before_fen: str
    after_fen: str
    piece: str
    captured_piece: str | None
    captured_color: str | None
    promotion: str | None
    is_capture: bool
    is_check: bool
    is_checkmate: bool
    is_castling: bool
    before: PositionFacts
    after: PositionFacts
    material_change: MaterialChange
    terminal: TerminalResult | None


@dataclass(frozen=True)
class PositionAnalysis:
    fen: str
    side_to_move: str
    board_uri: str
    terminal: TerminalResult | None
    actual_move: ActualMoveFacts | None


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
    fen: str


@dataclass(frozen=True)
class CandidateLine:
    rank: int
    depth: int
    evaluation: Evaluation
    moves: tuple[LineMove, ...]


@dataclass(frozen=True)
class MoveAssessment:
    quality: MoveQuality
    cpl: int | None
    mate_loss: bool
    best_move: LineMove
    actual_move: LineMove
    best_evaluation: Evaluation
    actual_evaluation: Evaluation | None
    is_forced: bool
    terminal: TerminalResult | None


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


def _side_name(board: chess.Board) -> str:
    return "white" if board.turn == chess.WHITE else "black"


def _material_summary(board: chess.Board) -> MaterialSummary:
    points = {chess.WHITE: 0, chess.BLACK: 0}
    pieces = {chess.WHITE: 0, chess.BLACK: 0}
    for piece in board.piece_map().values():
        if piece.piece_type == chess.KING:
            continue
        points[piece.color] += _MATERIAL_POINTS[piece.piece_type]
        pieces[piece.color] += 1
    return MaterialSummary(
        white_points=points[chess.WHITE],
        black_points=points[chess.BLACK],
        white_pieces=pieces[chess.WHITE],
        black_pieces=pieces[chess.BLACK],
    )


def _position_facts(board: chess.Board) -> PositionFacts:
    return PositionFacts(
        fen=board.fen(),
        side_to_move=_side_name(board),
        legal_move_count=board.legal_moves.count(),
        check=board.is_check(),
        castling_rights=board.castling_xfen(),
        material=_material_summary(board),
    )


def _board_uri(board: chess.Board) -> str:
    rendered = render_fen(
        board.fen(),
        RenderOptions(
            size=BOARD_SIZE,
            coordinates=True,
            output_format="svg",
        ),
    )
    return data_uri(rendered.media_type, rendered.image_bytes)


def _san_parts(value: str) -> tuple[str, str]:
    normalized = value.replace("0-0-0", "O-O-O").replace("0-0", "O-O")
    if normalized.endswith(("+", "#")):
        return normalized[:-1], normalized[-1]
    return normalized, ""


def _validate_san_text(value: str, canonical: str) -> None:
    entered_core, entered_suffix = _san_parts(value)
    canonical_core, canonical_suffix = _san_parts(canonical)
    valid_suffix = (
        entered_suffix == ""
        or entered_suffix == canonical_suffix
        or (entered_suffix == "+" and canonical_suffix == "#")
    )
    if entered_core != canonical_core or not valid_suffix:
        raise AnalysisInputError("invalid_move")


def parse_actual_move(board: chess.Board, value: str) -> chess.Move:
    text = value.strip()
    if not text:
        raise AnalysisInputError("invalid_move")
    if _UCI_RE.fullmatch(text):
        try:
            move = chess.Move.from_uci(text.lower())
        except ValueError as exc:
            raise AnalysisInputError("invalid_move") from exc
        if move not in board.legal_moves:
            raise AnalysisInputError("invalid_move")
        return move
    try:
        move = board.parse_san(text)
    except ValueError as exc:
        raise AnalysisInputError("invalid_move") from exc
    if move is None or move == chess.Move.null() or move not in board.legal_moves:
        raise AnalysisInputError("invalid_move")
    _validate_san_text(text, board.san(move))
    return move


def assess_actual_move(board: chess.Board, value: str) -> ActualMoveFacts:
    before = _position_facts(board)
    move = parse_actual_move(board, value)
    san = board.san(move)
    moved_piece = board.piece_at(move.from_square)
    if moved_piece is None:
        raise AnalysisInputError("invalid_move")

    captured_piece = board.piece_at(move.to_square)
    if captured_piece is None and board.is_en_passant(move):
        captured_square = move.to_square + (
            -8 if board.turn == chess.WHITE else 8
        )
        captured_piece = board.piece_at(captured_square)

    after_board = board.copy(stack=False)
    after_board.push(move)
    after = _position_facts(after_board)
    return ActualMoveFacts(
        uci=move.uci(),
        san=san,
        before_fen=before.fen,
        after_fen=after_board.fen(),
        piece=_PIECE_ROLES[moved_piece.piece_type],
        captured_piece=(
            _PIECE_ROLES[captured_piece.piece_type]
            if captured_piece is not None
            else None
        ),
        captured_color=(
            ("white" if captured_piece.color == chess.WHITE else "black")
            if captured_piece is not None
            else None
        ),
        promotion=(
            _PIECE_ROLES[move.promotion]
            if move.promotion is not None
            else None
        ),
        is_capture=board.is_capture(move),
        is_check=after_board.is_check(),
        is_checkmate=after_board.is_checkmate(),
        is_castling=board.is_castling(move),
        before=before,
        after=after,
        material_change=MaterialChange(
            white_points=after.material.white_points
            - before.material.white_points,
            black_points=after.material.black_points
            - before.material.black_points,
            white_pieces=after.material.white_pieces
            - before.material.white_pieces,
            black_pieces=after.material.black_pieces
            - before.material.black_pieces,
        ),
        terminal=terminal_result(after_board),
    )


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
                board.push(move)
                moves.append(LineMove(uci=uci, san=san, fen=board.fen()))
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


def _mover_score(evaluation: Evaluation, mover_is_white: bool) -> int:
    value = evaluation.value if mover_is_white else -evaluation.value
    if evaluation.kind == "mate":
        if value > 0:
            return _MATE_SCORE
        if value < 0:
            return -_MATE_SCORE
    return value


def _loss_for_move(
    best: Evaluation, actual: Evaluation, mover_is_white: bool
) -> tuple[int | None, bool]:
    best_score = _mover_score(best, mover_is_white)
    actual_score = _mover_score(actual, mover_is_white)
    if best_score >= _MATE_SCORE:
        if actual_score >= _MATE_SCORE:
            return 0, False
        return None, True
    if actual_score >= _MATE_SCORE:
        return 0, False
    if actual_score <= -_MATE_SCORE:
        return (None, True) if best_score > -_MATE_SCORE else (0, False)
    if best_score <= -_MATE_SCORE:
        return 0, False
    return max(0, best_score - actual_score), False


def evaluation_gap(
    best: Evaluation, alternative: Evaluation, mover_is_white: bool
) -> int:
    return max(
        0,
        _mover_score(best, mover_is_white)
        - _mover_score(alternative, mover_is_white),
    )


def evaluation_transition(
    before: Evaluation, after: Evaluation
) -> tuple[int, bool]:
    mate_changed = (before.kind == "mate") != (after.kind == "mate")
    if before.kind == "mate" or after.kind == "mate":
        if before.kind == after.kind == "mate" and before.value != after.value:
            mate_changed = True
        return 0, mate_changed
    return abs(after.value - before.value), False


def _move_quality(cpl: int, is_forced: bool) -> MoveQuality:
    if is_forced:
        return "forced"
    if cpl <= 10:
        return "best"
    if cpl <= 30:
        return "excellent"
    if cpl <= 70:
        return "good"
    if cpl <= 150:
        return "inaccuracy"
    if cpl <= 300:
        return "mistake"
    return "blunder"


def assess_position(
    fen: str,
    actual_value: str,
    before_candidates: list[CandidateInput],
    after_candidates: list[CandidateInput],
) -> tuple[tuple[CandidateLine, ...], MoveAssessment]:
    before_lines = candidate_lines(fen, before_candidates)
    if not before_lines:
        raise AnalysisInputError("invalid_candidate_line")
    root = parse_analysis_fen(fen)
    actual_uci = parse_actual_move(root, actual_value)
    actual_san = root.san(actual_uci)
    actual_board = root.copy(stack=False)
    actual_board.push(actual_uci)
    actual_move = LineMove(
        uci=actual_uci.uci(),
        san=actual_san,
        fen=actual_board.fen(),
    )
    is_forced = root.legal_moves.count() == 1
    best_evaluation = before_lines[0].evaluation
    terminal = terminal_result(actual_board)

    if terminal is not None and terminal.kind == "checkmate":
        assessment = MoveAssessment(
            quality=_move_quality(0, is_forced),
            cpl=0,
            mate_loss=False,
            best_move=before_lines[0].moves[0],
            actual_move=actual_move,
            best_evaluation=best_evaluation,
            actual_evaluation=None,
            is_forced=is_forced,
            terminal=terminal,
        )
        return before_lines, assessment

    if terminal is not None:
        actual_evaluation = Evaluation(kind="cp", value=0, text="0.00")
    else:
        if not after_candidates:
            raise AnalysisInputError("invalid_candidate_line")
        after_lines = candidate_lines(actual_board.fen(), after_candidates)
        if not after_lines:
            raise AnalysisInputError("invalid_candidate_line")
        actual_evaluation = after_lines[0].evaluation

    cpl, mate_loss = _loss_for_move(
        best_evaluation,
        actual_evaluation,
        root.turn == chess.WHITE,
    )
    quality_cpl = cpl if cpl is not None else _MATE_CPL
    assessment = MoveAssessment(
        quality=_move_quality(quality_cpl, is_forced),
        cpl=cpl,
        mate_loss=mate_loss,
        best_move=before_lines[0].moves[0],
        actual_move=actual_move,
        best_evaluation=best_evaluation,
        actual_evaluation=actual_evaluation,
        is_forced=is_forced,
        terminal=terminal,
    )
    return before_lines, assessment


def analyze_position(
    value: str, actual_move: str | None = None
) -> PositionAnalysis:
    board = parse_analysis_fen(value)
    actual_facts = (
        assess_actual_move(board, actual_move)
        if actual_move is not None and actual_move.strip()
        else None
    )
    return PositionAnalysis(
        fen=board.fen(),
        side_to_move=_side_name(board),
        board_uri=_board_uri(board),
        terminal=terminal_result(board),
        actual_move=actual_facts,
    )
