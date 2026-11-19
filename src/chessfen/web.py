from __future__ import annotations

import base64

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from chessfen.i18n import LOCALE_COOKIE, load_translations, negotiate_locale
from chessfen.render import (
    DEFAULT_SIZE,
    FenSyntaxError,
    RenderOptions,
    RenderOptionsError,
    parse_output_format,
    render_fen,
)
from chessfen.templating import render_response

router = APIRouter(prefix="/tools")

STARTING_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"

WARNING_LABELS: dict[str, str] = {
    "no_white_king": "No white king",
    "no_black_king": "No black king",
    "too_many_kings": "Too many kings",
    "too_many_white_pawns": "Too many white pawns",
    "too_many_black_pawns": "Too many black pawns",
    "pawns_on_backrank": "Pawns on the back rank",
    "too_many_white_pieces": "Too many white pieces",
    "too_many_black_pieces": "Too many black pieces",
    "bad_castling_rights": "Bad castling rights",
    "invalid_ep_square": "Invalid en passant square",
    "opposite_check": "The side not to move is in check",
    "empty": "Empty board",
    "too_many_checkers": "Too many checking pieces",
    "impossible_check": "Impossible check",
}


def _warning_labels(request: Request, codes: tuple[str, ...]) -> list[str]:
    locale = negotiate_locale(
        request.cookies.get(LOCALE_COOKIE), request.headers.get("accept-language")
    )
    translate = load_translations(locale).gettext
    return [translate(WARNING_LABELS.get(code, code)) for code in codes]


def _page(
    request: Request,
    *,
    form: dict[str, object],
    image_uri: str | None = None,
    warnings: list[str] | None = None,
    error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    return render_response(
        request,
        "render.html",
        {
            "form": form,
            "image_uri": image_uri,
            "warnings": warnings or [],
            "error": error,
        },
        status_code=status_code,
    )


@router.get("/render")
def render_page(request: Request) -> HTMLResponse:
    return _page(
        request,
        form={
            "fen": STARTING_FEN,
            "size": DEFAULT_SIZE,
            "flip": False,
            "coordinates": True,
            "format": "png",
        },
    )


@router.post("/render")
def render_submit(
    request: Request,
    fen: str = Form(""),
    size: str = Form(str(DEFAULT_SIZE)),
    flip: bool = Form(False),
    coordinates: bool = Form(False),
    format: str = Form("png"),
) -> HTMLResponse:
    form: dict[str, object] = {
        "fen": fen,
        "size": size,
        "flip": flip,
        "coordinates": coordinates,
        "format": format,
    }
    try:
        size_value = int(size)
    except ValueError:
        return _page(request, form=form, error="invalid_size", status_code=400)
    try:
        result = render_fen(
            fen,
            RenderOptions(
                size=size_value,
                flip=flip,
                coordinates=coordinates,
                output_format=parse_output_format(format),
            ),
        )
    except FenSyntaxError:
        return _page(request, form=form, error="invalid_fen", status_code=400)
    except RenderOptionsError as exc:
        return _page(request, form=form, error=exc.code, status_code=400)
    data_uri = (
        f"data:{result.media_type};base64,"
        + base64.b64encode(result.image_bytes).decode("ascii")
    )
    return _page(
        request,
        form=form,
        image_uri=data_uri,
        warnings=_warning_labels(request, result.warnings),
    )
