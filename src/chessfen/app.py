from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from itsdangerous import URLSafeTimedSerializer
from sqlalchemy.orm import sessionmaker

from chessfen.admin import AdminRequired
from chessfen.admin import router as admin_router
from chessfen.api import API_NAMESPACE, api_error, build_api_key_middleware
from chessfen.api import router as api_router
from chessfen.db import build_engine
from chessfen.detector import OnnxDetector
from chessfen.i18n import LOCALE_COOKIE, SUPPORTED_LOCALES
from chessfen.ratelimit import LoginRateLimiter
from chessfen.recognition import Recognizer
from chessfen.settings import DEFAULT_SECRET_KEY, Settings, get_settings
from chessfen.templating import render_response
from chessfen.web import router as tools_router

_ONE_YEAR_SECONDS = 60 * 60 * 24 * 365
_STATIC_DIR = Path(__file__).parent / "static"


def _is_same_origin_path(target: str) -> bool:
    return target.startswith("/") and not target.startswith(("//", "/\\"))


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or get_settings()
    if (
        resolved.admin_password is not None
        and resolved.secret_key == DEFAULT_SECRET_KEY
    ):
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
        resolved.secret_key, salt="chessfen-admin"
    )
    app.state.login_limiter = LoginRateLimiter(limit=5, window_seconds=60.0)
    app.state.recognizer = Recognizer(
        OnnxDetector(resolved.recognition_model_path)
    )
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")
    app.include_router(admin_router)
    app.include_router(api_router)
    app.include_router(tools_router)
    app.middleware("http")(build_api_key_middleware(app.state.session_factory))

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
