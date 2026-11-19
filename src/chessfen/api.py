from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, cast

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool

from chessfen.errors import CodedError
from chessfen.models import ApiKey
from chessfen.pgn import PgnError, count_plies, load_games, position_at, select_game
from chessfen.recognition import RecognitionError
from chessfen.render import (
    DEFAULT_SIZE,
    FenSyntaxError,
    RenderOptions,
    RenderOptionsError,
    parse_output_format,
    render_fen,
)
from chessfen.security import key_matches, parse_api_key

router = APIRouter(prefix="/api/v1")

API_NAMESPACE = "/api/"

API_ERRORS: dict[str, tuple[int, str]] = {
    "missing_api_key": (401, "An API key is required."),
    "invalid_api_key": (401, "The API key is invalid."),
    "api_key_revoked": (401, "The API key has been revoked."),
    "invalid_request": (422, "The request is invalid."),
    "invalid_fen": (400, "The FEN is invalid."),
    "invalid_size": (400, "The size must be between 64 and 2048."),
    "invalid_format": (400, "The format must be png or svg."),
    "invalid_pgn": (400, "The PGN could not be parsed."),
    "invalid_game": (400, "The game index is out of range."),
    "ply_out_of_range": (400, "The selected ply does not exist in this game."),
    "invalid_side": (400, "The side must be w or b."),
    "invalid_image": (400, "The image could not be decoded."),
    "no_board_detected": (400, "No chessboard was detected in the image."),
    "recognition_unavailable": (503, "Recognition is not available on this server."),
}


class ApiKeyAuthError(CodedError):
    pass


def api_error(code: str) -> JSONResponse:
    status, message = API_ERRORS[code]
    return JSONResponse(
        {"error": {"code": code, "message": message}}, status_code=status
    )


def authenticate_api_key(session: Session, raw_key: str | None) -> ApiKey:
    """Return the key on success or raise ApiKeyAuthError on failure.

    Usage is recorded with a single atomic UPDATE so concurrent requests with
    the same key cannot lose increments.
    """
    if not raw_key:
        raise ApiKeyAuthError("missing_api_key")
    parsed = parse_api_key(raw_key)
    if parsed is None:
        raise ApiKeyAuthError("invalid_api_key")
    prefix, _secret = parsed
    key = session.scalar(select(ApiKey).where(ApiKey.prefix == prefix))
    if key is None or not key_matches(raw_key, key.key_hash):
        raise ApiKeyAuthError("invalid_api_key")
    if key.revoked_at is not None:
        raise ApiKeyAuthError("api_key_revoked")
    session.execute(
        update(ApiKey)
        .where(ApiKey.id == key.id)
        .values(
            call_count=ApiKey.call_count + 1,
            last_used_at=datetime.now(timezone.utc),
        )
    )
    session.commit()
    session.refresh(key)
    return key


def build_api_key_middleware(session_factory: sessionmaker) -> Any:
    async def api_key_gate(request: Request, call_next: Any) -> Any:
        if not request.url.path.startswith(API_NAMESPACE):
            return await call_next(request)
        session = session_factory()
        try:
            key = await run_in_threadpool(
                authenticate_api_key, session, request.headers.get("x-api-key")
            )
        except ApiKeyAuthError as exc:
            return api_error(exc.code)
        finally:
            session.close()
        request.state.api_key = key
        return await call_next(request)

    return api_key_gate


@router.get("/me")
def me(request: Request) -> JSONResponse:
    key = cast(ApiKey, request.state.api_key)
    return JSONResponse(
        {"name": key.name, "prefix": key.prefix, "calls": key.call_count}
    )


class RenderPayload(BaseModel):
    fen: str
    size: int = DEFAULT_SIZE
    flip: bool = False
    coordinates: bool = True
    format: str = "png"


@router.post("/render")
def render(payload: RenderPayload) -> Response:
    try:
        options = RenderOptions(
            size=payload.size,
            flip=payload.flip,
            coordinates=payload.coordinates,
            output_format=parse_output_format(payload.format),
        )
        result = render_fen(payload.fen, options)
    except FenSyntaxError:
        return api_error("invalid_fen")
    except RenderOptionsError as exc:
        return api_error(exc.code)
    headers: dict[str, str] = {}
    if result.warnings:
        headers["X-ChessFEN-Warnings"] = ",".join(result.warnings)
    return Response(
        content=result.image_bytes,
        media_type=result.media_type,
        headers=headers,
    )


class PgnPayload(BaseModel):
    pgn: str
    game_index: int = 0
    ply: int = 0


@router.post("/pgn")
def pgn_navigation(payload: PgnPayload) -> JSONResponse:
    try:
        games = load_games(payload.pgn)
        game = select_game(games, payload.game_index)
        board = position_at(game, payload.ply)
    except PgnError as exc:
        return api_error(exc.code)
    return JSONResponse(
        {
            "fen": board.fen(),
            "game_index": payload.game_index,
            "ply": payload.ply,
            "available_plies": count_plies(game),
        }
    )


@router.post("/recognize")
def recognize_image(
    request: Request,
    image: UploadFile = File(...),
    side: str = Form("w"),
) -> JSONResponse:
    try:
        result = request.app.state.recognizer.recognize(
            image.file.read(), side_to_move=side
        )
    except RecognitionError as exc:
        return api_error(exc.code)
    return JSONResponse(
        {
            "placement": result.placement,
            "fen": result.fen,
            "side_to_move": result.side_to_move,
            "placement_derived": True,
            "orientation": result.orientation,
            "unmatched": result.unmatched,
            "squares": [
                {
                    "square": square.square,
                    "piece": square.piece,
                    "confidence": square.confidence,
                }
                for square in result.squares
            ],
        }
    )
