from __future__ import annotations

from collections.abc import Callable

from fastapi.testclient import TestClient

from chessfen.limits import IMAGE_MAX_BYTES, PGN_MAX_BYTES
from chessfen.recognition import Recognizer
from tests.helpers import EN, FakeDetector, starting_detections

_STARTING_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


def _recognize(
    client: TestClient,
    api_key: str,
    *,
    image_bytes: bytes = b"fake-image",
):
    return client.post(
        "/api/v1/recognize",
        files={"image": ("board.png", image_bytes, "image/png")},
        data={},
        headers={**EN, "X-API-Key": api_key},
    )


def test_api_rejects_oversized_image(
    client: TestClient, api_key: str
) -> None:
    response = _recognize(
        client, api_key, image_bytes=b"0" * (IMAGE_MAX_BYTES + 1)
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "image_too_large"


def test_api_rejects_oversized_pgn(client: TestClient, api_key: str) -> None:
    response = client.post(
        "/api/v1/pgn",
        json={"pgn": "x" * (PGN_MAX_BYTES + 1)},
        headers={"X-API-Key": api_key},
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "pgn_too_large"


def test_api_rejects_declared_oversized_upload_before_parsing(
    client: TestClient, api_key: str
) -> None:
    response = _recognize(
        client,
        api_key,
        image_bytes=b"0" * (IMAGE_MAX_BYTES + 70_000),
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "image_too_large"


def test_web_rejects_oversized_image(
    client_factory: Callable[..., TestClient],
) -> None:
    client = client_factory(
        recognizer=Recognizer(FakeDetector(starting_detections()))
    )

    page = client.post(
        "/tools/recognize",
        files={
            "image": ("board.png", b"0" * (IMAGE_MAX_BYTES + 1), "image/png")
        },
    )

    assert page.status_code == 413
    assert "图片不能超过 10 MB" in page.text


def test_web_rejects_oversized_pgn(client: TestClient) -> None:
    page = client.post(
        "/tools/pgn", data={"pgn": "x" * (PGN_MAX_BYTES + 1)}
    )

    assert page.status_code == 413
    assert "PGN 不能超过 1 MB" in page.text


def test_recognition_web_rate_limit(
    client_factory: Callable[..., TestClient],
) -> None:
    client = client_factory(
        recognizer=Recognizer(FakeDetector(starting_detections()))
    )

    for _ in range(20):
        page = client.post(
            "/tools/recognize",
            files={"image": ("board.png", b"fake", "image/png")},
        )
        assert page.status_code == 200

    blocked = client.post(
        "/tools/recognize",
        files={"image": ("board.png", b"fake", "image/png")},
    )

    assert blocked.status_code == 429
    assert "请求过多" in blocked.text


def test_render_web_rate_limit(client: TestClient) -> None:
    data = {"fen": _STARTING_FEN, "size": "100", "format": "png"}

    for _ in range(20):
        page = client.post("/tools/render", data=data)
        assert page.status_code == 200

    blocked = client.post("/tools/render", data=data)

    assert blocked.status_code == 429
    assert "请求过多" in blocked.text
    assert blocked.headers.get("retry-after") == "60"

    german = client.post(
        "/tools/render", data=data, headers={"Accept-Language": "de"}
    )
    assert german.status_code == 429
    assert "Zu viele Anfragen" in german.text