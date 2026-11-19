from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, cast

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool

from chessfen.models import ApiKey
from chessfen.security import key_matches, parse_api_key

router = APIRouter(prefix="/api/v1")

API_NAMESPACE = "/api/"

API_ERRORS: dict[str, tuple[int, str]] = {
    "missing_api_key": (401, "An API key is required."),
    "invalid_api_key": (401, "The API key is invalid."),
    "api_key_revoked": (401, "The API key has been revoked."),
}


class ApiKeyAuthError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


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
