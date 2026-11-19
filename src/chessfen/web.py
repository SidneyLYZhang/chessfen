from __future__ import annotations

import base64

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse

from chessfen.api import API_ERRORS
from chessfen.i18n import LOCALE_COOKIE, load_translations, negotiate_locale
from chessfen.limits import IMAGE_MAX_BYTES, PGN_MAX_BYTES
from chessfen.pgn import (
    GameSummary,
    PgnError,
    load_games,
    ply_for_move,
    position_at,
    select_game,
    summarize,
)
from chessfen.recognition import RecognitionError, RecognitionResult
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


def _consume_request_quota(request: Request) -> bool:
    limiter = request.app.state.request_limiter
    client_ip = request.client.host if request.client else "unknown"
    return not limiter.allow(client_ip)


def _rate_limited_response(response: HTMLResponse) -> HTMLResponse:
    response.headers["Retry-After"] = "60"
    return response


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
    if _consume_request_quota(request):
        return _rate_limited_response(
            _page(request, form=form, error="rate_limited", status_code=429)
        )
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


def _pgn_page(
    request: Request,
    *,
    form: dict[str, object],
    games: list[GameSummary] | None = None,
    result_fen: str | None = None,
    error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    return render_response(
        request,
        "pgn.html",
        {
            "form": form,
            "games": games or [],
            "result_fen": result_fen,
            "error": error,
        },
        status_code=status_code,
    )


def _pgn_error(
    request: Request,
    form: dict[str, object],
    summaries: list[GameSummary],
    code: str,
) -> HTMLResponse:
    return _pgn_page(
        request, form=form, games=summaries, error=code, status_code=400
    )


def _decode_pgn_bytes(raw: bytes) -> str:
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


@router.get("/pgn")
def pgn_page(request: Request) -> HTMLResponse:
    return _pgn_page(
        request,
        form={"pgn": "", "game_index": "", "move_number": "1", "side": "white"},
    )


@router.post("/pgn")
def pgn_submit(
    request: Request,
    pgn: str = Form(""),
    pgn_file: UploadFile | None = File(None),
    game_index: str = Form(""),
    move_number: str = Form("1"),
    side: str = Form("white"),
) -> HTMLResponse:
    if pgn_file is not None and pgn_file.filename:
        raw = pgn_file.file.read()
        if len(raw) > PGN_MAX_BYTES:
            return _pgn_page(
                request,
                form={
                    "pgn": "",
                    "game_index": "",
                    "move_number": move_number,
                    "side": side,
                },
                error="pgn_too_large",
                status_code=API_ERRORS["pgn_too_large"][0],
            )
        pgn = _decode_pgn_bytes(raw)
    elif len(pgn.encode("utf-8")) > PGN_MAX_BYTES:
        # Defence in depth: Starlette usually rejects a >1 MB form field first
        # and the HTTP-exception handler translates that to this same page.
        return _pgn_page(
            request,
            form={
                "pgn": "",
                "game_index": "",
                "move_number": move_number,
                "side": side,
            },
            error="pgn_too_large",
            status_code=API_ERRORS["pgn_too_large"][0],
        )
    form: dict[str, object] = {
        "pgn": pgn,
        "game_index": game_index,
        "move_number": move_number,
        "side": side,
    }
    try:
        games = load_games(pgn)
    except PgnError as exc:
        return _pgn_page(request, form=form, error=exc.code, status_code=400)
    summaries = [summarize(game, index) for index, game in enumerate(games)]
    if game_index == "":
        return _pgn_page(request, form=form, games=summaries)
    try:
        index = int(game_index)
    except ValueError:
        return _pgn_error(request, form, summaries, "invalid_game")
    try:
        move = int(move_number)
    except ValueError:
        return _pgn_error(request, form, summaries, "invalid_move")
    try:
        ply = ply_for_move(move, black=side == "black")
        game = select_game(games, index)
        board = position_at(game, ply)
    except PgnError as exc:
        return _pgn_error(request, form, summaries, exc.code)
    return _pgn_page(
        request, form=form, games=summaries, result_fen=board.fen()
    )


def _recognize_page(
    request: Request,
    *,
    form: dict[str, object],
    result: RecognitionResult | None = None,
    preview_uri: str | None = None,
    error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    return render_response(
        request,
        "recognize.html",
        {
            "form": form,
            "result": result,
            "preview_uri": preview_uri,
            "error": error,
        },
        status_code=status_code,
    )


@router.get("/recognize")
def recognize_page(request: Request) -> HTMLResponse:
    return _recognize_page(request, form={"side": "w"})


def oversized_upload_page(
    request: Request, code: str
) -> HTMLResponse | None:
    status_code = API_ERRORS[code][0]
    if request.url.path == "/tools/pgn":
        return _pgn_page(
            request,
            form={
                "pgn": "",
                "game_index": "",
                "move_number": "1",
                "side": "white",
            },
            error=code,
            status_code=status_code,
        )
    if request.url.path == "/tools/recognize":
        return _recognize_page(
            request, form={"side": "w"}, error=code, status_code=status_code
        )
    return None


@router.post("/recognize")
def recognize_submit(
    request: Request,
    image: UploadFile | None = File(None),
    side: str = Form("w"),
) -> HTMLResponse:
    form: dict[str, object] = {"side": side}
    if _consume_request_quota(request):
        return _rate_limited_response(
            _recognize_page(
                request, form=form, error="rate_limited", status_code=429
            )
        )
    if image is None or not image.filename:
        return _recognize_page(
            request, form=form, error="no_image", status_code=400
        )
    raw = image.file.read()
    if len(raw) > IMAGE_MAX_BYTES:
        return _recognize_page(
            request,
            form=form,
            error="image_too_large",
            status_code=API_ERRORS["image_too_large"][0],
        )
    media_type = (
        image.content_type
        if image.content_type and image.content_type.startswith("image/")
        else "image/png"
    )
    preview_uri = (
        f"data:{media_type};base64,"
        + base64.b64encode(raw).decode("ascii")
    )
    try:
        result = request.app.state.recognizer.recognize(
            raw, side_to_move=side
        )
    except RecognitionError as exc:
        status_code = API_ERRORS.get(exc.code, (400, ""))[0]
        return _recognize_page(
            request,
            form=form,
            preview_uri=preview_uri if exc.code != "invalid_image" else None,
            error=exc.code,
            status_code=status_code,
        )
    return _recognize_page(
        request, form=form, result=result, preview_uri=preview_uri
    )
