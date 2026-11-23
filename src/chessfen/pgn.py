from __future__ import annotations

import io
from dataclasses import dataclass

import chess
import chess.pgn

from chessfen.errors import CodedError


class PgnError(CodedError):
    pass


@dataclass(frozen=True)
class GameSummary:
    index: int
    white_player: str
    black_player: str
    result: str


@dataclass(frozen=True)
class MoveEntry:
    ply: int
    number: int
    side: str
    san: str


@dataclass(frozen=True)
class MoveRow:
    number: int
    white: MoveEntry | None
    black: MoveEntry | None


def load_games(pgn_text: str) -> list[chess.pgn.Game]:
    """Parse every Game in a PGN document.

    A document is invalid when parsing raises, when a game carries parser
    errors, when no game exists, or when no game has any mainline move
    (python-chess turns arbitrary text into an empty game, which is useless
    here). Parser errors include illegal moves inside variations; python-chess
    can drop or corrupt the remaining mainline after such an error, so the game
    is rejected rather than serving a possibly wrong Position.
    """
    stream = io.StringIO(pgn_text)
    games: list[chess.pgn.Game] = []
    while True:
        try:
            game = chess.pgn.read_game(stream)
        except ValueError as exc:
            raise PgnError("invalid_pgn") from exc
        if game is None:
            break
        if game.errors:
            raise PgnError("invalid_pgn")
        games.append(game)
    if not games or sum(count_plies(game) for game in games) == 0:
        raise PgnError("invalid_pgn")
    return games


def count_plies(game: chess.pgn.Game) -> int:
    return sum(1 for _ in game.mainline_moves())


def mainline_rows(game: chess.pgn.Game) -> list[MoveRow]:
    """Build the Mainline move grid, one row per full move.

    The side and full move number come from the board, so a Game that starts
    from a FEN with Black to move yields an opening row with no White entry.
    """
    board = game.board()
    rows: list[MoveRow] = []
    for ply, move in enumerate(game.mainline_moves(), start=1):
        side = "white" if board.turn else "black"
        entry = MoveEntry(
            ply=ply,
            number=board.fullmove_number,
            side=side,
            san=board.san(move),
        )
        if side == "white" or not rows:
            rows.append(
                MoveRow(
                    number=entry.number,
                    white=entry if side == "white" else None,
                    black=None if side == "white" else entry,
                )
            )
        else:
            previous = rows[-1]
            rows[-1] = MoveRow(
                number=previous.number,
                white=previous.white,
                black=entry,
            )
        board.push(move)
    return rows


def select_game(games: list[chess.pgn.Game], game_index: int) -> chess.pgn.Game:
    if game_index < 0 or game_index >= len(games):
        raise PgnError("invalid_game")
    return games[game_index]


def position_at(game: chess.pgn.Game, ply: int) -> chess.Board:
    moves = list(game.mainline_moves())
    if ply < 0 or ply > len(moves):
        raise PgnError("ply_out_of_range")
    board = game.board()
    for move in moves[:ply]:
        board.push(move)
    return board


def ply_for_move(
    move_number: int, *, black: bool, starts_black: bool = False
) -> int:
    """Map "after White/Black's Nth move" onto a ply (half-move count).

    Ply 0 is the initial Position. For a Game that starts with White,
    White's Nth move ends at ply 2N-1 and Black's Nth move at ply 2N;
    ``starts_black`` flips that mapping for FEN-started Games where Black
    moves first.
    """
    if move_number < 0:
        raise PgnError("ply_out_of_range")
    if move_number == 0:
        return 0
    if black == starts_black:
        return 2 * move_number - 1
    return 2 * move_number


def summarize(game: chess.pgn.Game, index: int) -> GameSummary:
    return GameSummary(
        index=index,
        white_player=game.headers.get("White", "?"),
        black_player=game.headers.get("Black", "?"),
        result=game.headers.get("Result", "*"),
    )
