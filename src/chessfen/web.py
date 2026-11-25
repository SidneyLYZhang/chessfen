from __future__ import annotations

from urllib.parse import urlencode

import chess
from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse

from chessfen.api import API_ERRORS
from chessfen.i18n import LOCALE_COOKIE, load_translations, negotiate_locale
from chessfen.limits import IMAGE_MAX_BYTES, PGN_MAX_BYTES
from chessfen.pgn import (
    GameSummary,
    MoveRow,
    PgnError,
    count_plies,
    load_games,
    mainline_rows,
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
    RenderResult,
    data_uri,
    image_data_uri,
    parse_fen,
    parse_output_format,
    render_fen,
)
from chessfen.templating import render_response

router = APIRouter(prefix="/tools")

STARTING_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"

EXAMPLE_POSITIONS: tuple[tuple[str, str], ...] = (
    ("Start position", STARTING_FEN),
    (
        "Italian Game",
        "r1bqkb1r/pppp1ppp/2n2n2/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4",
    ),
    (
        "King and pawn endgame",
        "8/8/4k3/8/8/4K3/4P3/8 w - - 0 1",
    ),
)

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


def _board_preview(fen: str) -> RenderResult:
    return render_fen(
        fen,
        RenderOptions(size=DEFAULT_SIZE, coordinates=True),
    )


def _default_render_form() -> dict[str, object]:
    return {
        "fen": STARTING_FEN,
        "size": DEFAULT_SIZE,
        "flip": False,
        "coordinates": True,
        "format": "png",
    }


def _query_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.lower() in {"1", "true", "on", "yes"}


def _render_examples() -> list[dict[str, str]]:
    return [
        {
            "label": label,
            "href": (
                "/tools/render?"
                + urlencode({"fen": fen, "coordinates": "on"})
            ),
        }
        for label, fen in EXAMPLE_POSITIONS
    ]


def _fen_summary(fen: str) -> dict[str, str]:
    board = parse_fen(fen)
    return {
        "side": "w" if board.turn else "b",
        "castling": board.castling_xfen(),
        "en_passant": (
            chess.square_name(board.ep_square)
            if board.ep_square is not None
            else "-"
        ),
        "halfmove": str(board.halfmove_clock),
        "fullmove": str(board.fullmove_number),
    }


def _render_page(
    request: Request,
    *,
    form: dict[str, object],
    image_uri: str | None = None,
    warnings: list[str] | None = None,
    summary: dict[str, str] | None = None,
    result_meta: dict[str, object] | None = None,
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
            "summary": summary,
            "result_meta": result_meta,
            "examples": _render_examples(),
            "error": error,
        },
        status_code=status_code,
    )


def _render_action(
    request: Request, form: dict[str, object], *, apply_rate_limit: bool = True
) -> HTMLResponse:
    if apply_rate_limit and _consume_request_quota(request):
        return _rate_limited_response(
            _render_page(request, form=form, error="rate_limited", status_code=429)
        )
    try:
        size_value = int(str(form["size"]))
    except ValueError:
        return _render_page(request, form=form, error="invalid_size", status_code=400)
    flip = bool(form["flip"])
    coordinates = bool(form["coordinates"])
    try:
        output_format = parse_output_format(str(form["format"]))
        result = render_fen(
            str(form["fen"]),
            RenderOptions(
                size=size_value,
                flip=flip,
                coordinates=coordinates,
                output_format=output_format,
            ),
        )
    except FenSyntaxError:
        return _render_page(request, form=form, error="invalid_fen", status_code=400)
    except RenderOptionsError as exc:
        return _render_page(request, form=form, error=exc.code, status_code=400)
    image_uri = image_data_uri(result)
    return _render_page(
        request,
        form=form,
        image_uri=image_uri,
        warnings=_warning_labels(request, result.warnings),
        summary=_fen_summary(str(form["fen"])),
        result_meta={
            "format": output_format,
            "size": size_value,
            "flip": flip,
            "coordinates": coordinates,
        },
    )


@router.get("/render")
def render_page(request: Request) -> HTMLResponse:
    # Example links use GET so they render without JavaScript; the initial
    # page (no fen parameter) stays an empty workspace with the start FEN ready.
    params = request.query_params
    if "fen" not in params:
        return _render_page(request, form=_default_render_form())
    form: dict[str, object] = {
        "fen": params.get("fen", STARTING_FEN),
        "size": params.get("size", str(DEFAULT_SIZE)),
        "flip": _query_bool(params.get("flip"), False),
        "coordinates": _query_bool(params.get("coordinates"), True),
        "format": params.get("format", "png"),
    }
    return _render_action(request, form, apply_rate_limit=False)


@router.post("/render")
def render_submit(
    request: Request,
    fen: str = Form(""),
    size: str = Form(str(DEFAULT_SIZE)),
    flip: bool = Form(False),
    coordinates: bool = Form(False),
    format: str = Form("png"),
) -> HTMLResponse:
    # Checkboxes are omitted from the payload when unchecked, so the server
    # defaults stay False; the initial page renders coordinates checked and
    # the example links pass coordinates=on explicitly.
    return _render_action(
        request,
        {
            "fen": fen,
            "size": size,
            "flip": flip,
            "coordinates": coordinates,
            "format": format,
        },
    )


def _default_pgn_form() -> dict[str, object]:
    return {
        "pgn": "",
        "game_index": "",
        "move_number": "1",
        "side": "white",
    }


def _pgn_page(
    request: Request,
    *,
    form: dict[str, object],
    games: list[GameSummary] | None = None,
    result_fen: str | None = None,
    board_uri: str | None = None,
    move_rows: list[MoveRow] | None = None,
    selected_game: GameSummary | None = None,
    selected_ply: int | None = None,
    total_plies: int | None = None,
    side_to_move: str | None = None,
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
            "board_uri": board_uri,
            "move_rows": move_rows or [],
            "selected_game": selected_game,
            "selected_ply": selected_ply,
            "total_plies": total_plies,
            "side_to_move": side_to_move,
            "board_size": DEFAULT_SIZE,
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
    return _pgn_page(request, form=_default_pgn_form())


@router.post("/pgn")
def pgn_submit(
    request: Request,
    pgn: str = Form(""),
    pgn_file: UploadFile | None = File(None),
    game_index: str = Form(""),
    move_number: str = Form("1"),
    side: str = Form("white"),
    mode: str = Form(""),
    ply: str = Form(""),
) -> HTMLResponse:
    if pgn_file is not None and pgn_file.filename:
        raw = pgn_file.file.read()
        if len(raw) > PGN_MAX_BYTES:
            error_form = _default_pgn_form()
            error_form["move_number"] = move_number
            error_form["side"] = side
            return _pgn_page(
                request,
                form=error_form,
                error="pgn_too_large",
                status_code=API_ERRORS["pgn_too_large"][0],
            )
        pgn = _decode_pgn_bytes(raw)
    elif len(pgn.encode("utf-8")) > PGN_MAX_BYTES:
        # Defence in depth: Starlette usually rejects a >1 MB form field first
        # and the HTTP-exception handler translates that to this same page.
        error_form = _default_pgn_form()
        error_form["move_number"] = move_number
        error_form["side"] = side
        return _pgn_page(
            request,
            form=error_form,
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
    if mode == "parse" or game_index == "":
        return _pgn_page(request, form=form, games=summaries)
    try:
        index = int(game_index)
    except ValueError:
        return _pgn_error(request, form, summaries, "invalid_game")
    try:
        game = select_game(games, index)
    except PgnError as exc:
        return _pgn_error(request, form, summaries, exc.code)
    # Parse/Open submit mode=...; the move grid submits ply; the precise
    # controls submit neither and fall back to move_number/side. The grid's
    # own numbering is the source of truth for FEN-started Games.
    move_rows = mainline_rows(game)
    starts_black = not game.board().turn
    if mode == "open":
        selected_ply = 0
    elif ply:
        try:
            selected_ply = int(ply)
        except ValueError:
            return _pgn_error(request, form, summaries, "invalid_ply")
    else:
        try:
            move = int(move_number)
        except ValueError:
            return _pgn_error(request, form, summaries, "invalid_move")
        try:
            selected_ply = ply_for_move(
                move_rows, move, black=side == "black"
            )
        except PgnError as exc:
            return _pgn_error(request, form, summaries, exc.code)
    try:
        board = position_at(game, selected_ply)
    except PgnError as exc:
        return _pgn_error(request, form, summaries, exc.code)
    board_result = _board_preview(board.fen())
    if selected_ply == 0:
        form["move_number"] = "0"
        form["side"] = "black" if starts_black else "white"
    else:
        selected_entry = next(
            entry
            for row in move_rows
            for entry in (row.white, row.black)
            if entry is not None and entry.ply == selected_ply
        )
        form["move_number"] = str(selected_entry.number)
        form["side"] = selected_entry.side
    form["game_index"] = str(index)
    return _pgn_page(
        request,
        form=form,
        games=summaries,
        result_fen=board.fen(),
        board_uri=image_data_uri(board_result),
        move_rows=move_rows,
        selected_game=summaries[index],
        selected_ply=selected_ply,
        total_plies=count_plies(game),
        side_to_move="w" if board.turn else "b",
    )


def _recognize_page(
    request: Request,
    *,
    form: dict[str, object],
    result: RecognitionResult | None = None,
    preview_uri: str | None = None,
    board_uri: str | None = None,
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
            "board_uri": board_uri,
            "board_size": DEFAULT_SIZE,
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
            form=_default_pgn_form(),
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
    preview_uri = data_uri(media_type, raw)
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
    board_uri: str | None = None
    try:
        board_result = _board_preview(result.placement)
        board_uri = image_data_uri(board_result)
    except FenSyntaxError:
        board_uri = None
    return _recognize_page(
        request,
        form=form,
        result=result,
        preview_uri=preview_uri,
        board_uri=board_uri,
    )
