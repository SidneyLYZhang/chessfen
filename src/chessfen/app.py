from __future__ import annotations

import mimetypes
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from itsdangerous import URLSafeTimedSerializer
from sqlalchemy.orm import sessionmaker
from starlette.exceptions import HTTPException as StarletteHTTPException

from chessfen.admin import AdminRequired
from chessfen.admin import router as admin_router
from chessfen.api import API_NAMESPACE, api_error, build_api_key_middleware
from chessfen.api import router as api_router
from chessfen.db import build_engine
from chessfen.detector import OnnxDetector
from chessfen.i18n import LOCALE_COOKIE, SUPPORTED_LOCALES
from chessfen.limits import IMAGE_MAX_BYTES, PGN_MAX_BYTES
from chessfen.pages import router as pages_router
from chessfen.ratelimit import LoginRateLimiter, RequestRateLimiter
from chessfen.recognition import Recognizer
from chessfen.settings import DEFAULT_SECRET_KEY, Settings, get_settings
from chessfen.templating import render_response
from chessfen.web import oversized_upload_page
from chessfen.web import router as tools_router

_ONE_YEAR_SECONDS = 60 * 60 * 24 * 365
_STATIC_DIR = Path(__file__).parent / "static"
mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("image/vnd.microsoft.icon", ".ico")
_UPLOAD_OVERHEAD = 64 * 1024
_UPLOAD_LIMITS: dict[str, tuple[int, str]] = {
    "/tools/recognize": (IMAGE_MAX_BYTES + _UPLOAD_OVERHEAD, "image_too_large"),
    "/api/v1/recognize": (IMAGE_MAX_BYTES + _UPLOAD_OVERHEAD, "image_too_large"),
    "/tools/pgn": (PGN_MAX_BYTES + _UPLOAD_OVERHEAD, "pgn_too_large"),
    "/api/v1/pgn": (PGN_MAX_BYTES + _UPLOAD_OVERHEAD, "pgn_too_large"),
}


def _is_same_origin_path(target: str) -> bool:
    return target.startswith("/") and not target.startswith(("//", "/\\"))


async def upload_size_guard(request: Request, call_next):
    # Reject clearly oversized bodies from their declared length before
    # Starlette spools or parses them. Exact limits are enforced after
    # reading (web) or parsing (API).
    if request.method == "POST":
        entry = _UPLOAD_LIMITS.get(request.url.path)
        if entry is not None:
            limit, code = entry
            length = request.headers.get("content-length", "")
            if length.isdigit() and int(length) > limit:
                if request.url.path.startswith("/api/"):
                    return api_error(code)
                page = oversized_upload_page(request, code)
                if page is not None:
                    return page
    return await call_next(request)


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or get_settings()
    if resolved.admin_password is not None and resolved.secret_key is None:
        raise RuntimeError(
            "SECRET_KEY must be set when ADMIN_PASSWORD is configured"
        )
    engine = build_engine(resolved.database_url)

    app = FastAPI(title="ChessFEN")
    app.state.settings = resolved
    app.state.session_factory = sessionmaker(
        bind=engine, autoflush=False, expire_on_commit=False
    )
    app.state.admin_session_serializer = URLSafeTimedSerializer(
        resolved.secret_key or DEFAULT_SECRET_KEY, salt="chessfen-admin"
    )
    app.state.login_limiter = LoginRateLimiter(limit=5, window_seconds=60.0)
    app.state.request_limiter = RequestRateLimiter(
        limit=20, window_seconds=60.0
    )
    app.state.recognizer = Recognizer(
        OnnxDetector(resolved.recognition_model_path)
    )
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")
    app.include_router(admin_router)
    app.include_router(api_router)
    app.include_router(tools_router)
    app.include_router(pages_router)
    app.middleware("http")(build_api_key_middleware(app.state.session_factory))
    app.middleware("http")(upload_size_guard)

    @app.exception_handler(AdminRequired)
    async def _admin_required(
        _request: Request, _exc: AdminRequired
    ) -> RedirectResponse:
        return RedirectResponse("/admin/login", status_code=303)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(
        request: Request, _exc: RequestValidationError
    ) -> JSONResponse:
        if request.url.path.startswith(API_NAMESPACE):
            return api_error("invalid_request")
        return JSONResponse({"detail": "Invalid request."}, status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def _http_exception(
        request: Request, exc: StarletteHTTPException
    ) -> Response:
        # Starlette's form parser enforces its own 1 MB part limit and answers
        # 400 before our handlers see the field; translate that specific parse
        # failure into the localized too-large page.
        length = request.headers.get("content-length", "")
        detail = str(exc.detail).lower()
        if (
            exc.status_code == 400
            and request.method == "POST"
            and request.url.path == "/tools/pgn"
            and ("maximum size" in detail or "error parsing the body" in detail)
            and length.isdigit()
            and int(length) > PGN_MAX_BYTES
        ):
            page = oversized_upload_page(request, "pgn_too_large")
            if page is not None:
                return page
        if (
            exc.status_code == 404
            and request.method in {"GET", "HEAD"}
            and not (
                request.url.path == "/api"
                or request.url.path.startswith("/api/")
            )
            and not request.url.path.startswith("/static/")
        ):
            return render_response(request, "404.html", status_code=404)
        return await http_exception_handler(request, exc)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/")
    def home(request: Request):
        return render_response(request, "index.html")

    @app.get("/lang/{code}")
    def set_language(code: str, request: Request) -> RedirectResponse:
        if code not in SUPPORTED_LOCALES:
            raise HTTPException(status_code=404)
        target = request.query_params.get("next") or "/"
        if not _is_same_origin_path(target):
            target = "/"
        response = RedirectResponse(target, status_code=303)
        response.set_cookie(
            LOCALE_COOKIE, code, max_age=_ONE_YEAR_SECONDS, samesite="lax"
        )
        return response

    return app


app = create_app()
