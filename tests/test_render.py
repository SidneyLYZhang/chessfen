from __future__ import annotations

import base64
import html
import io
import re
import struct
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient
from PIL import Image

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


def _example_hrefs(page_text: str) -> list[str]:
    return [
        html.unescape(match)
        for match in re.findall(r'href="(/tools/render\?fen=[^"]+)"', page_text)
    ]


def _data_uri_svg(page_text: str) -> str:
    match = re.search(
        r"data:image/svg\+xml;base64,([A-Za-z0-9+/=]+)", page_text
    )
    assert match is not None
    return base64.b64decode(match.group(1)).decode("utf-8")


def _data_uri_png(page_text: str) -> Image.Image:
    match = re.search(r"data:image/png;base64,([A-Za-z0-9+/=]+)", page_text)
    assert match is not None
    return Image.open(io.BytesIO(base64.b64decode(match.group(1)))).convert(
        "RGB"
    )


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


def test_render_page_starts_with_empty_workspace(client: TestClient) -> None:
    page = client.get("/tools/render", headers=_EN)

    assert page.status_code == 200
    assert _STARTING_FEN in page.text
    assert 'name="coordinates" checked' in page.text
    assert 'class="empty-state"' in page.text
    assert "The board appears here." in page.text
    assert "Submit a FEN or pick an example" in page.text
    assert "data:image" not in page.text


def test_render_example_links_render_server_side(client: TestClient) -> None:
    page = client.get("/tools/render", headers=_EN)
    hrefs = _example_hrefs(page.text)

    assert len(hrefs) == 3

    for href in hrefs:
        fen = parse_qs(urlparse(href).query)["fen"][0]
        example = client.get(href, headers=_EN)

        assert example.status_code == 200, href
        assert "data:image/png;base64," in example.text, href
        assert fen in example.text, href
        assert "Side to move" in example.text, href
        assert "Fullmove number" in example.text, href
        assert 'class="empty-state"' not in example.text, href


def test_render_examples_are_localized(client: TestClient) -> None:
    zh = client.get("/tools/render", headers={"Accept-Language": "zh"})
    de = client.get("/tools/render", headers={"Accept-Language": "de"})

    assert "初始局面" in zh.text
    assert "示例" in zh.text
    assert "Grundstellung" in de.text
    assert "Beispiele" in de.text


def test_render_form_shows_full_fen_summary(client: TestClient) -> None:
    page = client.post(
        "/tools/render",
        data={
            "fen": _STARTING_FEN,
            "size": "240",
            "coordinates": "on",
            "format": "png",
        },
        headers=_EN,
    )

    assert page.status_code == 200
    for label in (
        "Side to move",
        "Castling rights",
        "En passant",
        "Halfmove clock",
        "Fullmove number",
    ):
        assert label in page.text
    assert "White" in page.text
    assert "<dt>Castling rights</dt><dd>KQkq</dd>" in page.text


def test_render_result_uses_brand_palette_in_preview_and_api(
    client: TestClient, api_key: str
) -> None:
    png_page = client.post(
        "/tools/render",
        data={"fen": _STARTING_FEN, "coordinates": "on", "format": "png"},
        headers=_EN,
    )
    pixels = set(_data_uri_png(png_page.text).get_flattened_data())
    assert (0xF3, 0xF0, 0xE8) in pixels
    assert (0xBF, 0x81, 0x1D) in pixels

    page = client.post(
        "/tools/render",
        data={"fen": _STARTING_FEN, "coordinates": "on", "format": "svg"},
        headers=_EN,
    )
    svg = _data_uri_svg(page.text)

    for color in ("#F3F0E8", "#BF811D", "#3B2A17"):
        assert color in svg
    assert "#ffce9e" not in svg

    api_svg = _svg(client, api_key, coordinates=True)
    for color in ("#F3F0E8", "#BF811D", "#3B2A17"):
        assert color in api_svg


def test_render_coordinates_default_matches_the_form(
    client: TestClient,
) -> None:
    page = client.post(
        "/tools/render",
        data={"fen": _STARTING_FEN, "coordinates": "on", "format": "svg"},
        headers=_EN,
    )

    assert re.search(r"<dt>Coordinates</dt><dd>Yes</dd>", page.text)
    assert re.search(r"<dt>Flip board</dt><dd>No</dd>", page.text)


def test_render_download_names_the_output_file(client: TestClient) -> None:
    png = client.post(
        "/tools/render",
        data={"fen": _STARTING_FEN, "coordinates": "on", "format": "png"},
        headers=_EN,
    )
    assert 'download="chessfen-position.png"' in png.text

    svg = client.post(
        "/tools/render",
        data={"fen": _STARTING_FEN, "coordinates": "on", "format": "svg"},
        headers=_EN,
    )
    assert 'download="chessfen-position.svg"' in svg.text


def test_render_invalid_fen_is_announced_and_preserves_input(
    client: TestClient,
) -> None:
    page = client.post(
        "/tools/render",
        data={"fen": "not-a-fen", "size": "320", "format": "png"},
        headers=_EN,
    )

    assert page.status_code == 400
    assert 'role="alert"' in page.text
    assert "Invalid FEN" in page.text
    assert "not-a-fen" in page.text
    assert 'value="320"' in page.text


def test_render_illegal_position_uses_warning_list(client: TestClient) -> None:
    page = client.post(
        "/tools/render",
        data={"fen": _EMPTY_FEN, "size": "240", "format": "png"},
        headers=_EN,
    )

    assert page.status_code == 200
    assert 'class="warning-list"' in page.text
    assert "No white king" in page.text


def test_render_form_reports_out_of_range_options(
    client: TestClient,
) -> None:
    size_page = client.post(
        "/tools/render",
        data={"fen": _STARTING_FEN, "size": "10", "format": "png"},
        headers=_EN,
    )
    assert size_page.status_code == 400
    assert 'role="alert"' in size_page.text
    assert "Size must be between 64 and 2048." in size_page.text

    format_page = client.post(
        "/tools/render",
        data={"fen": _STARTING_FEN, "format": "pdf"},
        headers=_EN,
    )
    assert format_page.status_code == 400
    assert 'role="alert"' in format_page.text
    assert "Invalid format." in format_page.text


def test_render_form_preserves_size_and_flip(client: TestClient) -> None:
    page = client.post(
        "/tools/render",
        data={
            "fen": _STARTING_FEN,
            "size": "320",
            "flip": "on",
            "coordinates": "on",
            "format": "svg",
        },
        headers=_EN,
    )

    assert page.status_code == 200
    assert "<dt>Board size</dt><dd>320 px</dd>" in page.text
    assert "<dt>Flip board</dt><dd>Yes</dd>" in page.text

    svg = _data_uri_svg(page.text)
    assert 'width="320"' in svg
    assert _white_pawn_y(svg) < 195
