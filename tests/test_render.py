from __future__ import annotations

import re
import struct

from fastapi.testclient import TestClient

_EN = {"Accept-Language": "en"}
_STARTING_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
_EMPTY_FEN = "8/8/8/8/8/8/8/8 w - - 0 1"


def _render(client: TestClient, api_key: str, **overrides):
    payload = {"fen": _STARTING_FEN}
    payload.update(overrides)
    return client.post(
        "/api/v1/render", json=payload, headers={"X-API-Key": api_key}
    )


def _svg(client: TestClient, api_key: str, **overrides) -> str:
    response = _render(client, api_key, format="svg", **overrides)
    assert response.status_code == 200
    return response.content.decode("utf-8")


def _white_pawn_y(svg: str) -> int:
    match = re.search(
        r'<use href="#white-pawn"[^>]*transform="translate\(\d+, (\d+)\)"', svg
    )
    assert match is not None
    return int(match.group(1))


def test_api_renders_png(client: TestClient, api_key: str) -> None:
    response = _render(client, api_key)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/png")
    assert response.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_api_renders_svg_on_request(client: TestClient, api_key: str) -> None:
    response = _render(client, api_key, format="svg")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert b"<svg" in response.content[:200]


def test_api_honors_size_option(client: TestClient, api_key: str) -> None:
    response = _render(client, api_key, size=320)

    width, height = struct.unpack(">II", response.content[16:24])
    assert width == 320
    assert height == 320


def test_api_coordinates_option_draws_labels(
    client: TestClient, api_key: str
) -> None:
    with_coordinates = _svg(client, api_key, coordinates=True)
    without_coordinates = _svg(client, api_key, coordinates=False)

    assert with_coordinates.count("<g ") >= without_coordinates.count("<g ") + 16
    assert with_coordinates.count("<path") >= without_coordinates.count("<path") + 16


def test_api_flip_option_reorients_the_board(
    client: TestClient, api_key: str
) -> None:
    normal_y = _white_pawn_y(_svg(client, api_key, coordinates=True, flip=False))
    flipped_y = _white_pawn_y(_svg(client, api_key, coordinates=True, flip=True))

    assert normal_y > 195
    assert flipped_y < 195


def test_api_rejects_invalid_format(client: TestClient, api_key: str) -> None:
    response = _render(client, api_key, format="pdf")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_format"


def test_api_rejects_invalid_fen(client: TestClient, api_key: str) -> None:
    response = _render(client, api_key, fen="not-a-fen")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_fen"


def test_api_rejects_invalid_size(client: TestClient, api_key: str) -> None:
    response = _render(client, api_key, size=10)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_size"


def test_api_render_requires_key(client: TestClient) -> None:
    response = client.post("/api/v1/render", json={"fen": _STARTING_FEN})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "missing_api_key"


def test_api_illegal_position_renders_with_warnings(
    client: TestClient, api_key: str
) -> None:
    response = _render(client, api_key, fen=_EMPTY_FEN)

    assert response.status_code == 200
    warnings = response.headers.get("x-chessfen-warnings", "")
    assert "no_white_king" in warnings
    assert "no_black_king" in warnings


def test_api_invalid_payload_shape_is_structured(
    client: TestClient, api_key: str
) -> None:
    response = client.post(
        "/api/v1/render",
        json={"fen": _STARTING_FEN, "size": "big"},
        headers={"X-API-Key": api_key},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"


def test_home_links_to_render_tool(client: TestClient) -> None:
    page = client.get("/", headers=_EN)

    assert "/tools/render" in page.text


def test_render_page_serves_form_in_chinese(client: TestClient) -> None:
    page = client.get("/tools/render")

    assert page.status_code == 200
    assert "渲染 FEN" in page.text


def test_render_page_renders_in_german(client: TestClient) -> None:
    page = client.get("/tools/render", headers={"Accept-Language": "de"})

    assert "FEN rendern" in page.text


def test_render_form_returns_inline_image(client: TestClient) -> None:
    page = client.post(
        "/tools/render",
        data={"fen": _STARTING_FEN, "size": "240", "format": "png"},
        headers=_EN,
    )

    assert page.status_code == 200
    assert "data:image/png;base64," in page.text


def test_render_form_reports_warnings_for_illegal_position(
    client: TestClient,
) -> None:
    page = client.post(
        "/tools/render",
        data={"fen": _EMPTY_FEN, "size": "240", "format": "png"},
    )

    assert page.status_code == 200
    assert "缺少白王" in page.text
    assert "no_white_king" not in page.text


def test_render_form_localizes_invalid_fen(client: TestClient) -> None:
    page = client.post(
        "/tools/render",
        data={"fen": "not-a-fen", "size": "240", "format": "png"},
    )

    assert page.status_code == 400
    assert "FEN 格式无效" in page.text
