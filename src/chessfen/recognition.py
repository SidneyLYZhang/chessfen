from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol

from chessfen.errors import CodedError

BOARD_CLASS_ID = 0
RANK_ONE_ID_CLASS_ID = 13

PIECE_TO_FEN: dict[int, str] = {
    1: "p",
    2: "r",
    3: "b",
    4: "n",
    5: "k",
    6: "q",
    7: "P",
    8: "R",
    9: "B",
    10: "N",
    11: "K",
    12: "Q",
}

PIECE_CLASS_IDS = frozenset(PIECE_TO_FEN)


class RecognitionError(CodedError):
    pass


@dataclass(frozen=True)
class Detection:
    """One detected object in original image pixel coordinates."""

    class_id: int
    confidence: float
    xyxy: tuple[float, float, float, float]


class Detector(Protocol):
    def detect(self, image_bytes: bytes) -> list[Detection]:
        """Return detections in the coordinates of the given image bytes."""


@dataclass(frozen=True)
class SquareResult:
    square: str
    piece: str | None
    confidence: float | None


@dataclass(frozen=True)
class RecognitionResult:
    placement: str
    side_to_move: str
    orientation: str
    unmatched: int
    squares: tuple[SquareResult, ...]

    def fen_for(self, side_to_move: str) -> str:
        return f"{self.placement} {side_to_move} - - 0 1"

    @property
    def fen(self) -> str:
        return self.fen_for(self.side_to_move)


class Recognizer:
    def __init__(self, detector: Detector) -> None:
        self._detector = detector

    def recognize(
        self, image_bytes: bytes, side_to_move: str = "w"
    ) -> RecognitionResult:
        detections = self._detector.detect(image_bytes)
        return recognize_detections(detections, side_to_move=side_to_move)


def recognize_detections(
    detections: list[Detection], *, side_to_move: str = "w"
) -> RecognitionResult:
    """Build a Placement from detections.

    A Board image is expected to contain a single chessboard. When several are
    detected the highest-confidence one wins (larger area breaks ties); pieces
    on other detected boards are ignored. Pieces whose centre lies outside
    every detected board are reported as unmatched; when two pieces land on
    the same square of the chosen board the more confident one is kept.
    """
    if side_to_move not in ("w", "b"):
        raise RecognitionError("invalid_side")
    boards = [
        detection
        for detection in detections
        if detection.class_id == BOARD_CLASS_ID
        and detection.xyxy[2] > detection.xyxy[0]
        and detection.xyxy[3] > detection.xyxy[1]
    ]
    if not boards:
        raise RecognitionError("no_board_detected")
    board = max(boards, key=lambda item: (item.confidence, _area(item.xyxy)))

    orientation = "unknown"
    flipped = False
    markers = [
        detection
        for detection in detections
        if detection.class_id == RANK_ONE_ID_CLASS_ID
    ]
    if markers:
        marker = max(markers, key=lambda item: item.confidence)
        board_center_y = _center(board.xyxy)[1]
        marker_center_y = _center(marker.xyxy)[1]
        flipped = marker_center_y < board_center_y
        orientation = "flipped" if flipped else "normal"

    grid: list[list[tuple[int, float] | None]] = [
        [None] * 8 for _ in range(8)
    ]
    unmatched = 0
    for piece in detections:
        if piece.class_id not in PIECE_CLASS_IDS:
            continue
        cx, cy = _center(piece.xyxy)
        if not any(_inside(cx, cy, candidate.xyxy) for candidate in boards):
            unmatched += 1
            continue
        if not _inside(cx, cy, board.xyxy):
            continue
        row, col = _grid_cell(cx, cy, board.xyxy)
        current = grid[row][col]
        if current is None or piece.confidence > current[1]:
            grid[row][col] = (piece.class_id, piece.confidence)

    placement, squares = _build_placement(grid, flipped)
    return RecognitionResult(
        placement=placement,
        side_to_move=side_to_move,
        orientation=orientation,
        unmatched=unmatched,
        squares=squares,
    )


def _center(xyxy: tuple[float, float, float, float]) -> tuple[float, float]:
    return (xyxy[0] + xyxy[2]) / 2.0, (xyxy[1] + xyxy[3]) / 2.0


def _area(xyxy: tuple[float, float, float, float]) -> float:
    return max(0.0, xyxy[2] - xyxy[0]) * max(0.0, xyxy[3] - xyxy[1])


def _inside(
    cx: float, cy: float, xyxy: tuple[float, float, float, float]
) -> bool:
    return xyxy[0] <= cx <= xyxy[2] and xyxy[1] <= cy <= xyxy[3]


def _grid_cell(
    cx: float, cy: float, xyxy: tuple[float, float, float, float]
) -> tuple[int, int]:
    x1, y1, x2, y2 = xyxy
    col = int((cx - x1) / ((x2 - x1) / 8.0))
    row = int((cy - y1) / ((y2 - y1) / 8.0))
    return max(0, min(7, row)), max(0, min(7, col))


def _build_placement(
    grid: list[list[tuple[int, float] | None]], flipped: bool
) -> tuple[str, tuple[SquareResult, ...]]:
    ranks: list[str] = []
    squares: list[SquareResult] = []
    if flipped:
        row_order = range(7, -1, -1)
        col_order = range(7, -1, -1)
    else:
        row_order = range(8)
        col_order = range(8)

    def square_name(row: int, col: int) -> str:
        if flipped:
            return chr(ord("h") - col) + str(row + 1)
        return chr(ord("a") + col) + str(8 - row)

    for row in row_order:
        rank_parts: list[str] = []
        empty = 0
        for col in col_order:
            cell = grid[row][col]
            if cell is None:
                empty += 1
                squares.append(
                    SquareResult(square=square_name(row, col), piece=None, confidence=None)
                )
            else:
                class_id, confidence = cell
                if empty:
                    rank_parts.append(str(empty))
                    empty = 0
                piece = PIECE_TO_FEN[class_id]
                rank_parts.append(piece)
                squares.append(
                    SquareResult(
                        square=square_name(row, col),
                        piece=piece,
                        confidence=confidence,
                    )
                )
        if empty:
            rank_parts.append(str(empty))
        ranks.append("".join(rank_parts))
    return "/".join(ranks), tuple(squares)
