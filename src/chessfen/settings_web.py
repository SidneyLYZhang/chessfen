from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from chessfen.analysis_web import ANALYSIS_CSP
from chessfen.templating import render_response

router = APIRouter()


@router.get("/settings")
def settings_page(request: Request) -> HTMLResponse:
    response = render_response(request, "settings.html")
    response.headers["Content-Security-Policy"] = ANALYSIS_CSP
    response.headers["Cache-Control"] = "no-store"
    return response
