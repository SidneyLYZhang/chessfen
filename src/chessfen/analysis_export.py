from __future__ import annotations

from dataclasses import dataclass

import chess
import chess.pgn

from chessfen.analysis import (
    MoveQuality,
    ScoreKind,
    parse_analysis_fen,
)
from chessfen.errors import CodedError
from chessfen.pgn import load_games, select_game

_QUALITY_NAGS: dict[str, int] = {
    # Standard NAG classes; the distinct canonical Move quality remains in the
    # move comment so Best, Excellent, and Good are not conflated for readers.
    "best": 1,
    "excellent": 1,
    "inaccuracy": 6,
    "mistake": 2,
    "blunder": 4,
}


class ExportError(CodedError):
    pass


@dataclass(frozen=True)
class ExportExplanation:
    summary: str
    why: str
    alternatives: str
    uncertainty: str


@dataclass(frozen=True)
class PlyAnnotation:
    ply: int
    quality: MoveQuality
    score_kind: ScoreKind
    score_value: int
    cpl: int | None
    mate_loss: bool
    explanation: ExportExplanation | None = None


@dataclass(frozen=True)
class CandidateExport:
    rank: int
    score_kind: ScoreKind
    score_value: int
    moves: tuple[str, ...]


def _clamp_comment(value: str) -> str:
    text = " ".join(value.split())
    return text.replace("{", "(").replace("}", ")")


def _evaluation_tag(kind: ScoreKind, value: int) -> str:
    if kind == "mate":
        return f"#-{abs(value)}" if value < 0 else f"#{value}"
    return f"{value / 100:.2f}"


def _annotation_comment(
    annotation: PlyAnnotation,
    include_explanations: bool,
) -> str:
    parts = [
        f"[%eval {_evaluation_tag(annotation.score_kind, annotation.score_value)}]"
    ]
    if annotation.cpl is not None:
        parts.append(f"[%cpl {annotation.cpl}]")
    parts.append(f"Quality: {annotation.quality.capitalize()}.")
    if annotation.mate_loss:
        parts.append("Mate lost.")
    explanation = annotation.explanation
    if include_explanations and explanation is not None:
        parts.append(
            _clamp_comment(
                "Explanation: "
                f"{explanation.summary} | Why: {explanation.why} | "
                f"Alternatives: {explanation.alternatives} | "
                f"Uncertainty: {explanation.uncertainty}"
            )
        )
    return " ".join(parts)


def annotated_game_pgn(
    pgn_text: str,
    game_index: int,
    annotations: list[PlyAnnotation],
    *,
    include_explanations: bool,
) -> str:
    games = load_games(pgn_text)
    game = select_game(games, game_index)
    nodes: dict[int, chess.pgn.ChildNode] = {}
    node: chess.pgn.GameNode = game
    for ply, _move in enumerate(game.mainline_moves(), start=1):
        next_node = node.next()
        if next_node is None:
            raise ExportError("invalid_export")
        node = next_node
        nodes[ply] = node

    seen: set[int] = set()
    for annotation in annotations:
        if annotation.ply in seen or annotation.ply not in nodes:
            raise ExportError("invalid_export")
        seen.add(annotation.ply)
        target = nodes[annotation.ply]
        generated = _annotation_comment(annotation, include_explanations)
        target.comment = " ".join(
            part for part in [target.comment, generated] if part
        )
        nag = _QUALITY_NAGS.get(annotation.quality)
        if nag is not None:
            target.nags.add(nag)
    return str(game) + "\n"


def _candidate_comment(candidate: CandidateExport) -> str:
    evaluation = _evaluation_tag(candidate.score_kind, candidate.score_value)
    return f"#{candidate.rank} {evaluation}"


def position_pgn(
    fen: str,
    candidates: list[CandidateExport],
    comment: str | None,
    *,
    include_explanations: bool,
) -> str:
    board = parse_analysis_fen(fen)
    game = chess.pgn.Game()
    game.setup(board.fen())
    game.headers["SetUp"] = "1"
    game.headers["FEN"] = board.fen()
    game.headers.update(
        {
            "Event": "ChessFEN Position Analysis",
            "Site": "ChessFEN",
            "Date": "????.??.??",
            "Round": "-",
            "White": "?",
            "Black": "?",
            "Result": "*",
        }
    )
    if include_explanations and comment:
        game.comment = _clamp_comment(comment)

    for candidate in candidates:
        line_board = board.copy(stack=False)
        moves: list[chess.Move] = []
        for uci in candidate.moves:
            try:
                move = chess.Move.from_uci(uci)
            except ValueError as exc:
                raise ExportError("invalid_export") from exc
            if move not in line_board.legal_moves:
                raise ExportError("invalid_export")
            moves.append(move)
            line_board.push(move)
        if not moves:
            raise ExportError("invalid_export")
        # PGN has no variation slot before the first move. The first Candidate
        # Line is therefore the mainline and the rest are root variations; all
        # still begin from the supplied FEN without a fabricated predecessor.
        node: chess.pgn.GameNode = game
        for move_index, move in enumerate(moves):
            child = node.add_variation(move)
            if move_index == 0:
                child.comment = _candidate_comment(candidate)
            node = child
    return str(game) + "\n"
