from __future__ import annotations

import io
from dataclasses import asdict
from urllib.parse import urlsplit

import chess
from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from chessfen.analysis import (
    AnalysisInputError,
    CandidateInput,
    CandidateLine,
    MoveQuality,
    ScoreKind,
    analyze_position,
    assess_position,
    candidate_lines,
    terminal_result,
)
from chessfen.analysis_export import (
    CandidateExport,
    ExportExplanation,
    PlyAnnotation,
    annotated_game_pgn,
    position_pgn,
)
from chessfen.game_analysis import GameAnalysis, analyze_game
from chessfen.limits import PGN_MAX_BYTES
from chessfen.pgn import (
    PgnError,
    count_plies,
    load_games,
    select_game,
    summarize,
)
from chessfen.templating import render_response

router = APIRouter(prefix="/tools")


class FenRequest(BaseModel):
    fen: str = Field(max_length=200)


class PositionRequest(FenRequest):
    move: str | None = Field(default=None, max_length=30)


class ScoreRequest(BaseModel):
    kind: ScoreKind
    value: int = Field(ge=-100_000, le=100_000)


class LineRequest(BaseModel):
    multipv: int = Field(ge=1, le=5)
    depth: int = Field(ge=0, le=200)
    score: ScoreRequest
    pv: list[str] = Field(min_length=1, max_length=64)


class LinesRequest(FenRequest):
    lines: list[LineRequest] = Field(min_length=1, max_length=5)


class AssessmentRequest(LinesRequest):
    move: str = Field(min_length=1, max_length=30)
    after_lines: list[LineRequest] = Field(default_factory=list, max_length=5)


class GameLineRequest(BaseModel):
    lines: list[LineRequest] = Field(default_factory=list, max_length=5)


class GameResultsRequest(BaseModel):
    start_fen: str = Field(max_length=200)
    moves: list[str] = Field(max_length=400)
    positions: list[GameLineRequest] = Field(max_length=401)


class ExportExplanationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(max_length=4000)
    why: str = Field(max_length=4000)
    alternatives: str = Field(max_length=4000)
    uncertainty: str = Field(max_length=4000)


class PlyAnnotationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ply: int = Field(ge=1, le=400)
    quality: MoveQuality
    score_kind: ScoreKind
    score_value: int = Field(ge=-100_000, le=100_000)
    cpl: int | None = Field(default=None, ge=0)
    mate_loss: bool = False
    explanation: ExportExplanationRequest | None = None


class GamePgnExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pgn: str = Field(max_length=PGN_MAX_BYTES)
    game_index: int = Field(ge=0)
    include_explanations: bool = True
    annotations: list[PlyAnnotationRequest] = Field(max_length=400)


class PositionCandidateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rank: int = Field(ge=1, le=5)
    score_kind: ScoreKind
    score_value: int = Field(ge=-100_000, le=100_000)
    moves: list[str] = Field(min_length=1, max_length=64)


class PositionPgnExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fen: str = Field(max_length=200)
    include_explanations: bool = True
    comment: str | None = Field(default=None, max_length=20_000)
    candidates: list[PositionCandidateRequest] = Field(
        default_factory=list, max_length=5
    )


def _candidate_inputs(lines: list[LineRequest]) -> list[CandidateInput]:
    return [
        CandidateInput(
            multipv=line.multipv,
            depth=line.depth,
            score_kind=line.score.kind,
            score_value=line.score.value,
            pv=tuple(line.pv),
        )
        for line in lines
    ]


def _lines_payload(lines: tuple[CandidateLine, ...]) -> list[dict[str, object]]:
    return [
        {
            "rank": line.rank,
            "depth": line.depth,
            "evaluation": asdict(line.evaluation),
            "moves": [asdict(move) for move in line.moves],
        }
        for line in lines
    ]

ANALYSIS_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'wasm-unsafe-eval'; "
    "style-src 'self'; "
    "img-src 'self' data:; "
    "connect-src 'self' https:; "
    "worker-src 'self'; "
    "base-uri 'none'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)


def _analysis_headers(response: HTMLResponse) -> HTMLResponse:
    response.headers["Content-Security-Policy"] = ANALYSIS_CSP
    response.headers["Cache-Control"] = "no-store"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Cross-Origin-Embedder-Policy"] = "require-corp"
    return response


_JSON_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
}


def _json_response(
    payload: dict[str, object], status_code: int = 200
) -> JSONResponse:
    return JSONResponse(
        payload,
        status_code=status_code,
        headers=_JSON_HEADERS,
    )


def analysis_error(code: str, status_code: int = 400) -> JSONResponse:
    return _json_response(
        {"error": {"code": code}}, status_code=status_code
    )


def _is_same_origin(request: Request) -> bool:
    origin = request.headers.get("origin")
    host = request.headers.get("host")
    if not origin or not host:
        return False
    fetch_site = request.headers.get("sec-fetch-site")
    if fetch_site is not None and fetch_site != "same-origin":
        return False
    parsed = urlsplit(origin)
    return (
        parsed.scheme == request.url.scheme
        and parsed.netloc.lower() == host.lower()
    )


def _analysis_page_response(
    request: Request, template: str, context: dict[str, object] | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    return _analysis_headers(
        render_response(request, template, context, status_code=status_code)
    )


@router.get("/analysis")
def analysis_page(request: Request) -> HTMLResponse:
    return _analysis_page_response(request, "analysis.html")


def _decode_pgn_bytes(raw: bytes) -> str:
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def _pgn_page(
    request: Request,
    *,
    pgn: str = "",
    game_index: str = "",
    games: list[dict[str, object]] | None = None,
    game_data: dict[str, object] | None = None,
    error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    return _analysis_page_response(
        request,
        "analysis_pgn.html",
        {
            "pgn": pgn,
            "game_index": game_index,
            "games": games or [],
            "game_data": game_data,
            "error": error,
        },
        status_code,
    )


def _game_payload(game: chess.pgn.Game) -> dict[str, object]:
    count = count_plies(game)
    if count > 400:
        raise PgnError("analysis_too_long")
    board = game.board()
    start_fen = board.fen()
    positions = [start_fen]
    terminal_positions = [terminal_result(board) is not None]
    moves: list[dict[str, object]] = []
    for move in game.mainline_moves():
        before = board.fen()
        side = "white" if board.turn == chess.WHITE else "black"
        number = board.fullmove_number
        is_forced = board.legal_moves.count() == 1
        san = board.san(move)
        board.push(move)
        after = board.fen()
        positions.append(after)
        terminal_positions.append(terminal_result(board) is not None)
        moves.append(
            {
                "ply": len(moves) + 1,
                "number": number,
                "side": side,
                "san": san,
                "uci": move.uci(),
                "before_fen": before,
                "after_fen": after,
                "is_forced": is_forced,
            }
        )
    return {
        "start_fen": start_fen,
        "pgn": str(game),
        "positions": positions,
        "terminal_positions": terminal_positions,
        "moves": moves,
        "total_plies": count,
        "white": game.headers.get("White", "?"),
        "black": game.headers.get("Black", "?"),
        "result": game.headers.get("Result", "*"),
    }


@router.get("/analysis/pgn")
def analysis_pgn_page(request: Request) -> HTMLResponse:
    return _pgn_page(request)


@router.post("/analysis/pgn")
def analysis_pgn_submit(
    request: Request,
    pgn: str = Form(""),
    pgn_file: UploadFile | None = File(None),
    game_index: str = Form(""),
    mode: str = Form(""),
) -> HTMLResponse:
    if pgn_file is not None and pgn_file.filename:
        raw = pgn_file.file.read()
        if len(raw) > PGN_MAX_BYTES:
            return _pgn_page(
                request, pgn=pgn, error="pgn_too_large", status_code=400
            )
        pgn = _decode_pgn_bytes(raw)
    elif len(pgn.encode("utf-8")) > PGN_MAX_BYTES:
        return _pgn_page(
            request, pgn=pgn, error="pgn_too_large", status_code=400
        )
    try:
        games = load_games(pgn)
    except PgnError as exc:
        return _pgn_page(request, pgn=pgn, error=exc.code, status_code=400)
    summaries = [
        asdict(summarize(game, index)) for index, game in enumerate(games)
    ]
    if mode == "parse" or not game_index:
        return _pgn_page(request, pgn=pgn, games=summaries)
    try:
        index = int(game_index)
    except ValueError:
        return _pgn_page(
            request,
            pgn=pgn,
            games=summaries,
            error="invalid_game",
            status_code=400,
        )
    try:
        game = select_game(games, index)
        game_data = _game_payload(game)
    except PgnError as exc:
        return _pgn_page(
            request,
            pgn=pgn,
            game_index=str(index),
            games=summaries,
            error=exc.code,
            status_code=400,
        )
    return _pgn_page(
        request,
        pgn=pgn,
        game_index=str(index),
        games=summaries,
        game_data=game_data,
    )


@router.post("/analysis/game/results")
def analysis_game_results(
    request: Request, payload: GameResultsRequest
) -> JSONResponse:
    if not _is_same_origin(request):
        return analysis_error("invalid_origin", status_code=403)
    positions = [
        _candidate_inputs(position.lines) for position in payload.positions
    ]
    try:
        analysis = analyze_game(
            payload.start_fen, payload.moves, positions
        )
    except (AnalysisInputError, ValueError, chess.IllegalMoveError):
        return analysis_error("invalid_game_results")
    return _json_response(asdict(analysis))


@router.post("/analysis/export/game/pgn")
def analysis_export_game_pgn(
    request: Request, payload: GamePgnExportRequest
) -> JSONResponse:
    if not _is_same_origin(request):
        return analysis_error("invalid_origin", status_code=403)
    annotations = [
        PlyAnnotation(
            ply=item.ply,
            quality=item.quality,
            score_kind=item.score_kind,
            score_value=item.score_value,
            cpl=item.cpl,
            mate_loss=item.mate_loss,
            explanation=(
                ExportExplanation(
                    summary=item.explanation.summary,
                    why=item.explanation.why,
                    alternatives=item.explanation.alternatives,
                    uncertainty=item.explanation.uncertainty,
                )
                if item.explanation is not None
                else None
            ),
        )
        for item in payload.annotations
    ]
    try:
        content = annotated_game_pgn(
            payload.pgn,
            payload.game_index,
            annotations,
            include_explanations=payload.include_explanations,
        )
    except ValueError:
        return analysis_error("invalid_export")
    return _json_response(
        {
            "filename": "chessfen-game-analysis.pgn",
            "content": content,
        }
    )


@router.post("/analysis/export/position/pgn")
def analysis_export_position_pgn(
    request: Request, payload: PositionPgnExportRequest
) -> JSONResponse:
    if not _is_same_origin(request):
        return analysis_error("invalid_origin", status_code=403)
    candidates = [
        CandidateExport(
            rank=item.rank,
            score_kind=item.score_kind,
            score_value=item.score_value,
            moves=tuple(item.moves),
        )
        for item in payload.candidates
    ]
    try:
        content = position_pgn(
            payload.fen,
            candidates,
            payload.comment,
            include_explanations=payload.include_explanations,
        )
    except ValueError:
        return analysis_error("invalid_export")
    return _json_response(
        {
            "filename": "chessfen-position-analysis.pgn",
            "content": content,
        }
    )


@router.post("/analysis/position")
def analysis_position(
    request: Request, payload: PositionRequest
) -> JSONResponse:
    if not _is_same_origin(request):
        return analysis_error("invalid_origin", status_code=403)
    try:
        position = analyze_position(payload.fen, payload.move)
    except AnalysisInputError as exc:
        return analysis_error(exc.code)
    return _json_response(
        {
            "fen": position.fen,
            "side_to_move": position.side_to_move,
            "board_uri": position.board_uri,
            "terminal": (
                asdict(position.terminal) if position.terminal is not None else None
            ),
            "actual_move": (
                asdict(position.actual_move)
                if position.actual_move is not None
                else None
            ),
            "candidate_lines": [],
        }
    )


@router.post("/analysis/lines")
def analysis_lines(request: Request, payload: LinesRequest) -> JSONResponse:
    if not _is_same_origin(request):
        return analysis_error("invalid_origin", status_code=403)
    try:
        lines = candidate_lines(
            payload.fen, _candidate_inputs(payload.lines)
        )
    except AnalysisInputError as exc:
        return analysis_error(exc.code)
    return _json_response({"lines": _lines_payload(lines)})


@router.post("/analysis/assess")
def analysis_assess(
    request: Request, payload: AssessmentRequest
) -> JSONResponse:
    if not _is_same_origin(request):
        return analysis_error("invalid_origin", status_code=403)
    try:
        lines, assessment = assess_position(
            payload.fen,
            payload.move,
            _candidate_inputs(payload.lines),
            _candidate_inputs(payload.after_lines),
        )
    except AnalysisInputError as exc:
        return analysis_error(exc.code)
    return _json_response(
        {
            "lines": _lines_payload(lines),
            "assessment": asdict(assessment),
        }
    )
