from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from chessfen.templating import render_response

router = APIRouter()


@router.get("/learn/fen")
def fen_intro(request: Request) -> HTMLResponse:
    return render_response(request, "learn_fen.html")


@router.get("/learn/pgn")
def pgn_intro(request: Request) -> HTMLResponse:
    return render_response(request, "learn_pgn.html")


@router.get("/about")
def about(request: Request) -> HTMLResponse:
    return render_response(request, "about.html")
