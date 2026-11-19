from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from chessfen.i18n import SUPPORTED_LOCALES, negotiate_locale
from chessfen.templating import render_page

_ONE_YEAR_SECONDS = 60 * 60 * 24 * 365
_LOCALE_COOKIE = "lang"
_STATIC_DIR = Path(__file__).parent / "static"


def _is_same_origin_path(target: str) -> bool:
    return target.startswith("/") and not target.startswith(("//", "/\\"))


def _next_path(request: Request) -> str:
    query = request.url.query
    return f"{request.url.path}?{query}" if query else request.url.path


def create_app() -> FastAPI:
    app = FastAPI(title="ChessFEN")
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request) -> HTMLResponse:
        locale = negotiate_locale(
            request.cookies.get(_LOCALE_COOKIE), request.headers.get("accept-language")
        )
        return HTMLResponse(
            render_page(
                locale,
                "index.html",
                {
                    "request": request,
                    "locale": locale,
                    "next_path": _next_path(request),
                },
            )
        )

    @app.get("/lang/{code}")
    def set_language(code: str, request: Request) -> RedirectResponse:
        if code not in SUPPORTED_LOCALES:
            raise HTTPException(status_code=404)
        target = request.query_params.get("next") or "/"
        if not _is_same_origin_path(target):
            target = "/"
        response = RedirectResponse(target, status_code=303)
        response.set_cookie(
            _LOCALE_COOKIE, code, max_age=_ONE_YEAR_SECONDS, samesite="lax"
        )
        return response

    return app


app = create_app()
