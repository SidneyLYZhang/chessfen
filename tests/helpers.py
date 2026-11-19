from __future__ import annotations

import re
from typing import Any

from fastapi.testclient import TestClient

from chessfen.recognition import Detection, RecognitionError

EN = {"Accept-Language": "en"}
API_KEY_RE = re.compile(r"cf_[0-9a-f]{8}_[0-9a-f]{32}")

STARTING_PLACEMENT = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR"
BLACK_BACK_RANK = [2, 4, 3, 6, 5, 3, 4, 2]
WHITE_BACK_RANK = [8, 10, 9, 12, 11, 9, 10, 8]


class FakeDetector:
    def __init__(self, detections: list[Detection]) -> None:
        self._detections = detections

    def detect(self, image_bytes: bytes) -> list[Detection]:
        return list(self._detections)


class RaisingRecognizer:
    def __init__(self, code: str) -> None:
        self._code = code

    def recognize(self, image_bytes: bytes, side_to_move: str = "w") -> Any:
        raise RecognitionError(self._code)


def box(
    class_id: int,
    cx: float,
    cy: float,
    confidence: float = 0.9,
    half: float = 10.0,
) -> Detection:
    return Detection(
        class_id=class_id,
        confidence=confidence,
        xyxy=(cx - half, cy - half, cx + half, cy + half),
    )


def starting_detections(
    *, flipped: bool = False, marker: bool = True
) -> list[Detection]:
    detections = [Detection(0, 0.95, (0.0, 0.0, 800.0, 800.0))]

    def position(file_index: int, rank: int) -> tuple[float, float]:
        if flipped:
            return (7 - file_index + 0.5) * 100, (rank - 1 + 0.5) * 100
        return (file_index + 0.5) * 100, (8 - rank + 0.5) * 100

    for file_index, class_id in enumerate(BLACK_BACK_RANK):
        x, y = position(file_index, 8)
        detections.append(box(class_id, x, y))
    for file_index in range(8):
        x, y = position(file_index, 7)
        detections.append(box(1, x, y))
    for file_index in range(8):
        x, y = position(file_index, 2)
        detections.append(box(7, x, y))
    for file_index, class_id in enumerate(WHITE_BACK_RANK):
        x, y = position(file_index, 1)
        detections.append(box(class_id, x, y))
    if marker:
        detections.append(box(13, 790, 10 if flipped else 790))
    return detections


def authenticate_admin(client: TestClient) -> None:
    client.post(
        "/admin/login",
        data={"password": "test-admin"},
        headers=EN,
        follow_redirects=False,
    )
    response = client.post(
        "/admin/keys",
        data={"name": "tests"},
        headers=EN,
        follow_redirects=False,
    )
    match = API_KEY_RE.search(response.text)
    assert match is not None
    client.headers["X-API-Key"] = match.group(0)
