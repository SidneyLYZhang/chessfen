from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from chessfen.recognition import Recognizer
from tests.helpers import (
    EN,
    STARTING_PLACEMENT,
    FakeDetector,
    RaisingRecognizer,
    box,
    starting_detections,
)

_DE = {"Accept-Language": "de"}


@pytest.fixture
def web_client(client_factory: Callable[..., TestClient]) -> Iterator[TestClient]:
    client = client_factory(
        recognizer=Recognizer(FakeDetector(starting_detections()))
    )
    with client:
        yield client


def _upload(
    client: TestClient,
    *,
    side: str | None = None,
    image_bytes: bytes = b"fake-image",
    headers: dict[str, str] | None = None,
) -> Any:
    data = {"side": side} if side is not None else {}
    return client.post(
        "/tools/recognize",
        files={"image": ("board.png", image_bytes, "image/png")},
        data=data,
        headers=headers or {},
    )


def test_recognize_page_serves_form_in_chinese(web_client: TestClient) -> None:
    page = web_client.get("/tools/recognize")

    assert page.status_code == 200
    assert "识别棋盘" in page.text


def test_recognize_page_renders_in_german(web_client: TestClient) -> None:
    page = web_client.get("/tools/recognize", headers=_DE)

    assert "Brett erkennen" in page.text


def test_recognize_upload_shows_placement_and_squares(
    web_client: TestClient,
) -> None:
    page = _upload(web_client)

    assert page.status_code == 200
    assert STARTING_PLACEMENT in page.text
    assert f"{STARTING_PLACEMENT} w - - 0 1" in page.text
    assert 'id="show-confidence"' in page.text
    assert page.text.count('class="square"') == 64
    assert "a8" in page.text
    assert "0.90" in page.text
    assert "未匹配棋子" not in page.text
    assert 'data-copy-target="fen-text"' in page.text
    assert "复制 FEN" in page.text
    assert "data:image/png;base64,ZmFrZS1pbWFnZQ==" in page.text


def test_recognize_upload_side_override_changes_fen(
    web_client: TestClient,
) -> None:
    page = _upload(web_client, side="b")

    assert page.status_code == 200
    assert f"{STARTING_PLACEMENT} b - - 0 1" in page.text


def test_recognize_upload_reports_unmatched_pieces(
    client_factory: Callable[..., TestClient],
) -> None:
    detections = starting_detections()
    detections.append(box(7, 900, 50))
    detections.append(box(7, 950, 50))
    client = client_factory(recognizer=Recognizer(FakeDetector(detections)))

    page = _upload(client)

    assert page.status_code == 200
    assert "未匹配棋子：2" in page.text


def test_recognize_page_reports_no_board(
    client_factory: Callable[..., TestClient],
) -> None:
    client = client_factory(recognizer=Recognizer(FakeDetector([])))

    page = _upload(client)

    assert page.status_code == 400
    assert "未检测到棋盘" in page.text
    assert 'role="alert"' in page.text


def test_recognize_page_reports_invalid_image(
    client_factory: Callable[..., TestClient],
) -> None:
    client = client_factory(recognizer=RaisingRecognizer("invalid_image"))

    page = _upload(client)

    assert page.status_code == 400
    assert "图片无法识别" in page.text
    assert 'role="alert"' in page.text


def test_recognize_page_reports_unavailable(
    client_factory: Callable[..., TestClient],
) -> None:
    client = client_factory(
        recognizer=RaisingRecognizer("recognition_unavailable")
    )

    page = _upload(client)

    assert page.status_code == 503
    assert "服务器未启用识别" in page.text


def test_recognize_page_requires_an_image(web_client: TestClient) -> None:
    page = web_client.post("/tools/recognize", data={"side": "w"})

    assert page.status_code == 400
    assert "请选择图片文件" in page.text


def test_home_links_to_recognize_tool(client: TestClient) -> None:
    page = client.get("/", headers=EN)

    assert "/tools/recognize" in page.text


def test_recognize_page_starts_with_upload_workspace(
    web_client: TestClient,
) -> None:
    page = web_client.get("/tools/recognize", headers=EN)

    assert page.status_code == 200
    assert "data:image" not in page.text
    assert "Choose a board image" in page.text
    assert "A cropped, axis-aligned board image works best" in page.text
    assert 'name="image"' in page.text
    assert "The results appear here." in page.text
    assert "Upload a board image to start recognition." in page.text


def test_recognize_result_shows_source_and_recognized_board(
    web_client: TestClient,
) -> None:
    page = _upload(web_client, headers=EN)

    assert page.status_code == 200
    assert "Recognized position" in page.text
    assert 'alt="Board image"' in page.text
    assert 'class="result-board"' in page.text
    assert "data:image/png;base64,iVBOR" in page.text
    assert "data:image/png;base64,ZmFrZS1pbWFnZQ==" in page.text


def test_recognize_side_switch_data_is_rendered(
    web_client: TestClient,
) -> None:
    page = _upload(web_client, headers=EN)

    assert 'id="side-select"' in page.text
    assert 'data-fen-w="' in page.text
    assert 'data-fen-b="' in page.text
    assert 'getElementById("side-select")' not in page.text


def test_recognize_orientation_and_confidence_toggle(
    web_client: TestClient,
) -> None:
    page = _upload(web_client, headers=EN)

    assert "Orientation" in page.text
    assert "Normal" in page.text
    assert 'id="show-confidence"' in page.text
    assert 'for="show-confidence"' in page.text
    assert 'class="squares"' in page.text
    assert page.text.count('class="square"') == 64


def test_recognize_low_confidence_is_marked(
    client_factory: Callable[..., TestClient],
) -> None:
    detections = starting_detections()
    detections.append(box(7, 450, 450, confidence=0.5))
    client = client_factory(recognizer=Recognizer(FakeDetector(detections)))

    page = _upload(client, headers=EN)

    assert page.status_code == 200
    assert 'data-low="true"' in page.text
    assert "0.50" in page.text


def test_recognize_new_labels_are_localized(web_client: TestClient) -> None:
    zh = _upload(web_client, headers={"Accept-Language": "zh"})
    de = _upload(web_client, headers=_DE)

    assert "选择棋盘图片" in zh.text
    assert "识别出的局面" in zh.text
    assert "Brettbild auswählen" in de.text
    assert "Erkannte Stellung" in de.text


def test_recognize_errors_are_announced(web_client: TestClient) -> None:
    page = web_client.post(
        "/tools/recognize", data={}, headers={"Accept-Language": "en"}
    )

    assert page.status_code == 400
    assert 'role="alert"' in page.text
    assert "An image file is required." in page.text


def test_recognize_confidence_toggle_is_wired_without_js(
    web_client: TestClient,
) -> None:
    page = _upload(web_client, headers=EN)

    assert re.search(
        r'<section class="confidence-section">\s*'
        r'<input type="checkbox" id="show-confidence">',
        page.text,
    )
    assert page.text.index('id="show-confidence"') < page.text.index(
        'class="squares"'
    )
    assert 'for="show-confidence"' in page.text
