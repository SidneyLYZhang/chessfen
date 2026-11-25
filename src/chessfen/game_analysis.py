from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field

import chess

from chessfen.analysis import (
    CandidateInput,
    CandidateLine,
    Evaluation,
    MoveAssessment,
    assess_actual_move,
    assess_position,
    candidate_lines,
    evaluation_gap,
    evaluation_transition,
    parse_analysis_fen,
    terminal_result,
)

MAX_GAME_PLIES = 400
_KEY_QUALITIES = {"excellent", "inaccuracy", "mistake", "blunder"}


@dataclass(frozen=True)
class PositionIndicators:
    doubled_pawns: int
    isolated_pawns: int
    passed_pawns: int
    open_files: int
    king_zone_attackers: int


@dataclass(frozen=True)
class GamePly:
    ply: int
    number: int
    side: str
    uci: str
    san: str
    before_fen: str
    after_fen: str
    quality: str
    cpl: int | None
    mate_loss: bool
    is_forced: bool
    is_key: bool
    key_reasons: tuple[str, ...]
    evaluation_before: Evaluation
    evaluation_after: Evaluation
    exact_facts: Mapping[str, object]
    indicators: PositionIndicators


@dataclass(frozen=True)
class GameAnalysis:
    start_fen: str
    evaluations: tuple[Evaluation, ...]
    plies: tuple[GamePly, ...]
    key_plies: tuple[int, ...]
    summary: dict[str, int] = field(default_factory=dict)


def _pawns(board: chess.Board, color: chess.Color) -> dict[int, list[int]]:
    files: dict[int, list[int]] = {}
    for square in board.pieces(chess.PAWN, color):
        files.setdefault(chess.square_file(square), []).append(
            chess.square_rank(square)
        )
    return files


def position_indicators(board: chess.Board) -> PositionIndicators:
    white = _pawns(board, chess.WHITE)
    black = _pawns(board, chess.BLACK)

    def doubled(pawns: dict[int, list[int]]) -> int:
        return sum(max(0, len(ranks) - 1) for ranks in pawns.values())

    def isolated(pawns: dict[int, list[int]]) -> int:
        return sum(
            1
            for file, ranks in pawns.items()
            if (file - 1 not in pawns) and (file + 1 not in pawns)
            for _ in ranks
        )

    def passed(pawns: dict[int, list[int]], enemies: dict[int, list[int]], color: chess.Color) -> int:
        total = 0
        for file, ranks in pawns.items():
            enemy_files = (file - 1, file, file + 1)
            for rank in ranks:
                blockers = [
                    enemy_rank
                    for enemy_file in enemy_files
                    for enemy_rank in enemies.get(enemy_file, [])
                    if (enemy_rank > rank if color == chess.WHITE else enemy_rank < rank)
                ]
                if not blockers:
                    total += 1
        return total

    white_files = {chess.square_file(square) for square in board.pieces(chess.PAWN, chess.WHITE)}
    black_files = {chess.square_file(square) for square in board.pieces(chess.PAWN, chess.BLACK)}
    open_files = sum(1 for file in range(8) if file not in white_files and file not in black_files)

    king_zone = 0
    for color in (chess.WHITE, chess.BLACK):
        king = board.king(color)
        if king is None:
            continue
        files = range(max(0, chess.square_file(king) - 1), min(8, chess.square_file(king) + 2))
        ranks = range(max(0, chess.square_rank(king) - 1), min(8, chess.square_rank(king) + 2))
        attacked = {
            square
            for square in (
                chess.square(file, rank) for file in files for rank in ranks
            )
            if board.attackers(not color, square)
        }
        king_zone += len(attacked)

    return PositionIndicators(
        doubled_pawns=doubled(white) + doubled(black),
        isolated_pawns=isolated(white) + isolated(black),
        passed_pawns=passed(white, black, chess.WHITE)
        + passed(black, white, chess.BLACK),
        open_files=open_files,
        king_zone_attackers=king_zone,
    )


def _key_reasons(
    assessment: MoveAssessment,
    before_lines: tuple[CandidateLine, ...],
    root: chess.Board,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if assessment.quality in _KEY_QUALITIES:
        reasons.append("quality")
    if (
        assessment.quality in {"best", "good"}
        and len(before_lines) > 1
    ):
        gap = evaluation_gap(
            before_lines[0].evaluation,
            before_lines[1].evaluation,
            root.turn == chess.WHITE,
        )
        if gap >= 150:
            reasons.append("only_best")
    swing, mate_changed = evaluation_transition(
        assessment.best_evaluation, assessment.actual_evaluation or assessment.best_evaluation
    )
    if swing >= 150:
        reasons.append("evaluation_swing")
    if mate_changed or (
        assessment.terminal is not None
        and assessment.terminal.kind == "checkmate"
    ):
        reasons.append("mate_change")
    return tuple(dict.fromkeys(reasons))


def analyze_game(
    start_fen: str,
    ucis: list[str],
    positions: list[list[CandidateInput]],
) -> GameAnalysis:
    if len(ucis) > MAX_GAME_PLIES:
        raise ValueError("analysis_too_long")
    if len(positions) != len(ucis) + 1:
        raise ValueError("invalid_game_results")
    root = parse_analysis_fen(start_fen)
    evaluations: list[Evaluation] = []
    plies: list[GamePly] = []
    board = root.copy(stack=False)
    quality_counts = {
        "best": 0,
        "excellent": 0,
        "good": 0,
        "inaccuracy": 0,
        "mistake": 0,
        "blunder": 0,
        "forced": 0,
    }

    for index, uci in enumerate(ucis):
        before_lines, assessment = assess_position(
            board.fen(), uci, positions[index], positions[index + 1]
        )
        evaluations.append(before_lines[0].evaluation)
        exact = assess_actual_move(board, uci)
        facts = {
            "before_fen": exact.before_fen,
            "after_fen": exact.after_fen,
            "piece": exact.piece,
            "captured_piece": exact.captured_piece,
            "captured_color": exact.captured_color,
            "promotion": exact.promotion,
            "is_capture": exact.is_capture,
            "is_check": exact.is_check,
            "is_checkmate": exact.is_checkmate,
            "is_castling": exact.is_castling,
            "material_change": asdict(exact.material_change),
            "legal_move_count": exact.before.legal_move_count,
            "castling_rights": exact.after.castling_rights,
        }
        reasons = _key_reasons(assessment, before_lines, board)
        plies.append(
            GamePly(
                ply=index + 1,
                number=board.fullmove_number,
                side="white" if board.turn == chess.WHITE else "black",
                uci=uci,
                san=exact.san,
                before_fen=exact.before_fen,
                after_fen=exact.after_fen,
                quality=assessment.quality,
                cpl=assessment.cpl,
                mate_loss=assessment.mate_loss,
                is_forced=assessment.is_forced,
                is_key=bool(reasons),
                key_reasons=reasons,
                evaluation_before=assessment.best_evaluation,
                evaluation_after=(
                    assessment.actual_evaluation
                    or assessment.best_evaluation
                ),
                exact_facts=facts,
                indicators=position_indicators(
                    parse_analysis_fen(exact.after_fen)
                ),
            )
        )
        quality_counts[assessment.quality] += 1
        board.push(chess.Move.from_uci(uci))

    try:
        evaluations.append(
            _evaluation_for_position(board.fen(), positions[-1])
        )
    except ValueError:
        if plies and terminal_result(board) is not None:
            evaluations.append(plies[-1].evaluation_after)
        else:
            raise
    key_plies = tuple(ply.ply for ply in plies if ply.is_key)
    return GameAnalysis(
        start_fen=start_fen,
        evaluations=tuple(evaluations),
        plies=tuple(plies),
        key_plies=key_plies,
        summary=quality_counts,
    )


def _evaluation_for_position(
    fen: str, candidates: list[CandidateInput]
) -> Evaluation:
    lines = candidate_lines(fen, candidates)
    if not lines:
        raise ValueError("invalid_game_results")
    return lines[0].evaluation
