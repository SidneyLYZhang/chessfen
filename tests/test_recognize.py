from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from chessfen.app import create_app
from chessfen.recognition import (
    Detection,
    RecognitionError,
    Recognizer,
    recognize_detections,
)
from chessfen.settings import Settings
from tests.helpers import (
    EN,
    STARTING_PLACEMENT,
    FakeDetector,
    authenticate_admin,
    box,
    starting_detections,
)


def test_recognizer_builds_starting_placement() -> None:
    result = recognize_detections(starting_detections())

    assert result.placement == STARTING_PLACEMENT
    assert result.orientation == "normal"
    assert result.unmatched == 0
    assert result.side_to_move == "w"
    assert result.fen == f"{STARTING_PLACEMENT} w - - 0 1"
    assert len(result.squares) == 64
    assert result.squares[0].square == "a8"
    assert result.squares[0].piece == "r"
    assert result.squares[4].square == "e8"
    assert result.squares[4].piece == "k"
    assert result.squares[16].square == "a6"
    assert result.squares[16].piece is None
    assert result.squares[60].square == "e1"
    assert result.squares[60].piece == "K"
    assert result.squares[63].square == "h1"
    assert result.squares[63].piece == "R"


def test_recognizer_reads_flipped_orientation() -> None:
    result = recognize_detections(starting_detections(flipped=True))

    assert result.orientation == "flipped"
    assert result.placement == STARTING_PLACEMENT
    assert result.squares[0].piece == "r"


def test_recognizer_defaults_orientation_when_marker_missing() -> None:
    result = recognize_detections(starting_detections(marker=False))

    assert result.orientation == "unknown"
    assert result.placement == STARTING_PLACEMENT


def test_recognizer_counts_unmatched_pieces_outside_the_board() -> None:
    detections = starting_detections()
    detections.append(box(7, 900, 50))

    result = recognize_detections(detections)

    assert result.unmatched == 1
    assert result.placement == STARTING_PLACEMENT


def test_recognizer_keeps_most_confident_piece_per_square() -> None:
    detections = starting_detections()
    detections.append(box(7, 450, 450, confidence=0.5))
    detections.append(box(1, 450, 450, confidence=0.7))

    result = recognize_detections(detections)

    square = next(s for s in result.squares if s.square == "e4")
    assert square.piece == "p"
    assert result.unmatched == 0


def test_recognizer_ignores_pieces_of_other_detected_boards() -> None:
    detections = starting_detections()
    detections.append(
        Detection(0, 0.90, (900.0, 0.0, 1700.0, 800.0))
    )
    detections.append(box(7, 1300, 450))  # inside the second board only

    result = recognize_detections(detections)

    assert result.unmatched == 0
    assert result.placement == STARTING_PLACEMENT


def test_recognizer_honors_side_override() -> None:
    result = recognize_detections(starting_detections(), side_to_move="b")

    assert result.fen.endswith(" b - - 0 1")
    assert result.fen_for("w").endswith(" w - - 0 1")


def test_recognizer_rejects_invalid_side() -> None:
    recognizer = Recognizer(FakeDetector(starting_detections()))

    with pytest.raises(RecognitionError) as excinfo:
        recognizer.recognize(b"image", side_to_move="x")

    assert excinfo.value.code == "invalid_side"


def test_recognizer_requires_a_board() -> None:
    with pytest.raises(RecognitionError) as excinfo:
        recognize_detections([box(7, 100, 100)])

    assert excinfo.value.code == "no_board_detected"


@pytest.fixture
def rec_client(client_factory: Callable[..., TestClient]) -> Iterator[TestClient]:
    client = client_factory(
        recognizer=Recognizer(FakeDetector(starting_detections()))
    )
    with client:
        authenticate_admin(client)
        yield client


def _recognize(
    client: TestClient,
    *,
    data: dict[str, str] | None = None,
    image_bytes: bytes = b"fake-image",
) -> Any:
    return client.post(
        "/api/v1/recognize",
        files={"image": ("board.png", image_bytes, "image/png")},
        data=data or {},
        headers=EN,
    )


def test_api_returns_placement_and_squares(rec_client: TestClient) -> None:
    response = _recognize(rec_client)

    assert response.status_code == 200
    body = response.json()
    assert body["placement"] == STARTING_PLACEMENT
    assert body["fen"] == f"{STARTING_PLACEMENT} w - - 0 1"
    assert body["side_to_move"] == "w"
    assert body["placement_derived"] is True
    assert body["orientation"] == "normal"
    assert body["unmatched"] == 0
    assert len(body["squares"]) == 64
    assert body["squares"][0]["square"] == "a8"
    assert body["squares"][0]["piece"] == "r"
    assert body["squares"][0]["confidence"] == pytest.approx(0.9)


def test_api_accepts_side_override(rec_client: TestClient) -> None:
    response = _recognize(rec_client, data={"side": "b"})

    assert response.status_code == 200
    assert response.json()["fen"].endswith(" b - - 0 1")


def test_api_rejects_invalid_side(rec_client: TestClient) -> None:
    response = _recognize(rec_client, data={"side": "x"})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_side"


def test_api_recognize_requires_key(
    client_factory: Callable[..., TestClient],
) -> None:
    client = client_factory(
        recognizer=Recognizer(FakeDetector(starting_detections()))
    )

    response = _recognize(client)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "missing_api_key"


def test_api_reports_no_board(
    client_factory: Callable[..., TestClient],
) -> None:
    client = client_factory(recognizer=Recognizer(FakeDetector([])))
    with client:
        authenticate_admin(client)
        response = _recognize(client)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "no_board_detected"


def test_api_reports_missing_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_url = f"sqlite:///{(tmp_path / 'missing-model.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    command.upgrade(Config("alembic.ini"), "head")
    app = create_app(
        Settings(
            database_url=database_url,
            secret_key="test-secret",
            admin_password="test-admin",
            recognition_model_path=str(tmp_path / "absent.onnx"),
        )
    )
    with TestClient(app) as client:
        authenticate_admin(client)
        response = _recognize(client)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "recognition_unavailable"


_MODEL_PATH = (
    Path(__file__).resolve().parents[1] / "models" / "yolo26m-finetuned.onnx"
)


@pytest.mark.skipif(
    not _MODEL_PATH.is_file(), reason="ONNX weights not present"
)
def test_api_rejects_undecodable_image(
    client: TestClient, api_key: str
) -> None:
    response = client.post(
        "/api/v1/recognize",
        files={"image": ("x.bin", b"not an image", "application/octet-stream")},
        headers={"X-API-Key": api_key},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_image"
