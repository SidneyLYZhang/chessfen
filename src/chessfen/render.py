from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import chess
import chess.svg
import resvg_py

from chessfen.errors import CodedError

DEFAULT_SIZE = 480
MIN_SIZE = 64
MAX_SIZE = 2048

OutputFormat = Literal["png", "svg"]


class FenSyntaxError(ValueError):
    """The FEN string cannot be parsed."""


class RenderOptionsError(CodedError):
    """A rendering option is out of range or unsupported."""


@dataclass(frozen=True)
class RenderOptions:
    size: int = DEFAULT_SIZE
    flip: bool = False
    coordinates: bool = True
    output_format: OutputFormat = "png"


@dataclass(frozen=True)
class RenderResult:
    image_bytes: bytes
    media_type: str
    warnings: tuple[str, ...]


def parse_output_format(value: str) -> OutputFormat:
    if value == "png":
        return "png"
    if value == "svg":
        return "svg"
    raise RenderOptionsError("invalid_format")


def render_fen(fen: str, options: RenderOptions | None = None) -> RenderResult:
    resolved = options or RenderOptions()
    board = parse_fen(fen)
    if not MIN_SIZE <= resolved.size <= MAX_SIZE:
        raise RenderOptionsError("invalid_size")
    svg = chess.svg.board(
        board,
        size=resolved.size,
        flipped=resolved.flip,
        coordinates=resolved.coordinates,
    )
    warnings = position_warnings(board)
    if resolved.output_format == "svg":
        return RenderResult(svg.encode("utf-8"), "image/svg+xml", warnings)
    png = bytes(resvg_py.svg_to_bytes(svg_string=svg))
    return RenderResult(png, "image/png", warnings)


def parse_fen(fen: str) -> chess.Board:
    try:
        return chess.Board(fen)
    except ValueError as exc:
        raise FenSyntaxError(fen) from exc


def position_warnings(board: chess.Board) -> tuple[str, ...]:
    status = board.status()
    if status == chess.Status.VALID:
        return ()
    return tuple(
        (flag.name or "").lower()
        for flag in chess.Status
        if flag != chess.Status.VALID and status & flag
    )
