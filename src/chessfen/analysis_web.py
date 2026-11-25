from __future__ import annotations

from dataclasses import asdict
from urllib.parse import urlsplit

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from chessfen.analysis import (
    AnalysisInputError,
    CandidateInput,
    ScoreKind,
    analyze_position,
    candidate_lines,
)
from chessfen.templating import render_response

router = APIRouter(prefix="/tools")


class PositionRequest(BaseModel):
    fen: str = Field(max_length=200)


class ScoreRequest(BaseModel):
    kind: ScoreKind
    value: int = Field(ge=-100_000, le=100_000)


class LineRequest(BaseModel):
    multipv: int = Field(ge=1, le=5)
    depth: int = Field(ge=0, le=200)
    score: ScoreRequest
    pv: list[str] = Field(min_length=1, max_length=64)


class LinesRequest(PositionRequest):
    lines: list[LineRequest] = Field(min_length=1, max_length=5)

ANALYSIS_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'wasm-unsafe-eval'; "
    "style-src 'self'; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "worker-src 'self'; "
    "base-uri 'none'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)


def _analysis_headers(response: HTMLResponse) -> HTMLResponse:
    response.headers["Content-Security-Policy"] = ANALYSIS_CSP
    response.headers["Cache-Control"] = "no-store"
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


@router.get("/analysis")
def analysis_page(request: Request) -> HTMLResponse:
    return _analysis_headers(render_response(request, "analysis.html"))


@router.post("/analysis/position")
def analysis_position(
    request: Request, payload: PositionRequest
) -> JSONResponse:
    if not _is_same_origin(request):
        return analysis_error("invalid_origin", status_code=403)
    try:
        position = analyze_position(payload.fen)
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
            "candidate_lines": [],
        }
    )


@router.post("/analysis/lines")
def analysis_lines(request: Request, payload: LinesRequest) -> JSONResponse:
    if not _is_same_origin(request):
        return analysis_error("invalid_origin", status_code=403)
    candidates = [
        CandidateInput(
            multipv=line.multipv,
            depth=line.depth,
            score_kind=line.score.kind,
            score_value=line.score.value,
            pv=tuple(line.pv),
        )
        for line in payload.lines
    ]
    try:
        lines = candidate_lines(payload.fen, candidates)
    except AnalysisInputError as exc:
        return analysis_error(exc.code)
    return _json_response(
        {
            "lines": [
                {
                    "rank": line.rank,
                    "depth": line.depth,
                    "evaluation": asdict(line.evaluation),
                    "moves": [asdict(move) for move in line.moves],
                }
                for line in lines
            ]
        }
    )
