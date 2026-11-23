from __future__ import annotations

import hmac
from collections.abc import Iterator
from datetime import datetime, timezone
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature, SignatureExpired
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from chessfen.models import ApiKey
from chessfen.security import generate_api_key
from chessfen.templating import render_response

router = APIRouter(prefix="/admin")

ADMIN_COOKIE = "admin_session"
SESSION_MAX_AGE_SECONDS = 12 * 60 * 60


class AdminRequired(Exception):
    """Raised when an admin-only route is hit without a valid session."""


def _is_admin(request: Request) -> bool:
    value = request.cookies.get(ADMIN_COOKIE)
    if not value:
        return False
    try:
        data = request.app.state.admin_session_serializer.loads(
            value, max_age=SESSION_MAX_AGE_SECONDS
        )
    except (BadSignature, SignatureExpired):
        return False
    return data == {"admin": True}


def require_admin(request: Request) -> None:
    if not _is_admin(request):
        raise AdminRequired()


def _db_session(request: Request) -> Iterator[Session]:
    session = request.app.state.session_factory()
    try:
        yield session
    finally:
        session.close()


def _normalize_filters(q: str, status: str) -> tuple[str, str]:
    q = (q or "").strip()
    status = status or "all"
    if status not in {"all", "active", "revoked"}:
        status = "all"
    return q, status


def _read_filters(request: Request) -> tuple[str, str]:
    return _normalize_filters(
        request.query_params.get("q") or "",
        request.query_params.get("status") or "all",
    )


def _filter_query(q: str, status: str) -> str:
    values: dict[str, str] = {}
    if q:
        values["q"] = q
    if status != "all":
        values["status"] = status
    return urlencode(values)


def _like_pattern(q: str) -> str:
    escaped = (
        q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    )
    return f"%{escaped}%"


def _list_keys(
    session: Session, *, q: str = "", status: str = "all"
) -> list[ApiKey]:
    statement = select(ApiKey).order_by(ApiKey.id.desc())
    if q:
        pattern = _like_pattern(q)
        statement = statement.where(
            or_(
                ApiKey.name.ilike(pattern, escape="\\"),
                ApiKey.prefix.ilike(pattern, escape="\\"),
            )
        )
    if status == "active":
        statement = statement.where(ApiKey.revoked_at.is_(None))
    elif status == "revoked":
        statement = statement.where(ApiKey.revoked_at.is_not(None))
    return list(session.scalars(statement))


def _key_stats(session: Session) -> dict[str, int]:
    total = session.scalar(select(func.count()).select_from(ApiKey)) or 0
    active = (
        session.scalar(
            select(func.count())
            .select_from(ApiKey)
            .where(ApiKey.revoked_at.is_(None))
        )
        or 0
    )
    return {"total": total, "active": active, "revoked": total - active}


def _admin_context(
    session: Session,
    *,
    q: str = "",
    status: str = "all",
    new_key: str | None = None,
    error: str | None = None,
) -> dict[str, object]:
    return {
        "keys": _list_keys(session, q=q, status=status),
        "stats": _key_stats(session),
        "filters": {"q": q, "status": status},
        "filters_active": bool(q) or status != "all",
        "filter_query": _filter_query(q, status),
        "new_key": new_key,
        "error": error,
    }


@router.get("/login")
def login_page(request: Request):
    return render_response(
        request,
        "admin/login.html",
        {
            "error": None,
            "configured": request.app.state.settings.admin_password is not None,
        },
    )


@router.post("/login")
def login(request: Request, password: str = Form(...)):
    settings = request.app.state.settings
    limiter = request.app.state.login_limiter
    client_ip = request.client.host if request.client else "unknown"
    if limiter.is_limited(client_ip):
        limited = render_response(
            request,
            "admin/login.html",
            {
                "error": "rate_limited",
                "configured": settings.admin_password is not None,
            },
            status_code=429,
        )
        limited.headers["Retry-After"] = "60"
        return limited
    if settings.admin_password is None:
        return render_response(
            request, "admin/login.html", {"error": None, "configured": False}
        )
    if not hmac.compare_digest(
        password.encode("utf-8"), settings.admin_password.encode("utf-8")
    ):
        limiter.record_failure(client_ip)
        return render_response(
            request, "admin/login.html", {"error": "invalid", "configured": True}
        )
    limiter.reset(client_ip)
    response = RedirectResponse("/admin", status_code=303)
    token = request.app.state.admin_session_serializer.dumps({"admin": True})
    response.set_cookie(
        ADMIN_COOKIE,
        token,
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
    )
    return response


@router.post("/logout")
def logout() -> RedirectResponse:
    response = RedirectResponse("/admin/login", status_code=303)
    response.delete_cookie(ADMIN_COOKIE)
    return response


@router.get("")
def admin_home(
    request: Request,
    session: Session = Depends(_db_session),
    _admin: None = Depends(require_admin),
):
    q, status = _read_filters(request)
    return render_response(
        request,
        "admin/index.html",
        _admin_context(session, q=q, status=status),
    )


@router.post("/keys")
def create_key(
    request: Request,
    name: str = Form(...),
    q: str = Form(""),
    status: str = Form("all"),
    session: Session = Depends(_db_session),
    _admin: None = Depends(require_admin),
):
    q, status = _normalize_filters(q, status)
    cleaned = name.strip()
    if not cleaned:
        return render_response(
            request,
            "admin/index.html",
            _admin_context(
                session, q=q, status=status, error="name_required"
            ),
            status_code=422,
        )
    full_key, prefix, key_hash = generate_api_key()
    session.add(
        ApiKey(
            name=cleaned,
            prefix=prefix,
            key_hash=key_hash,
            created_at=datetime.now(timezone.utc),
            call_count=0,
        )
    )
    session.commit()
    return render_response(
        request,
        "admin/index.html",
        _admin_context(session, q=q, status=status, new_key=full_key),
    )


@router.post("/keys/{key_id}/revoke")
def revoke_key(
    request: Request,
    key_id: int,
    session: Session = Depends(_db_session),
    _admin: None = Depends(require_admin),
) -> RedirectResponse:
    key = session.get(ApiKey, key_id)
    if key is not None and key.revoked_at is None:
        key.revoked_at = datetime.now(timezone.utc)
        session.commit()
    q, status = _read_filters(request)
    target = "/admin"
    query = _filter_query(q, status)
    if query:
        target += f"?{query}"
    return RedirectResponse(target, status_code=303)
