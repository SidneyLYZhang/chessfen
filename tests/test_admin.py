from __future__ import annotations

import re
from typing import Any

import pytest
from fastapi.testclient import TestClient

from chessfen.app import create_app
from chessfen.settings import DEFAULT_SECRET_KEY, Settings

_EN = {"Accept-Language": "en"}
_KEY_RE = re.compile(r"cf_[0-9a-f]{8}_[0-9a-f]{32}")


def _extract_key(response: Any) -> str:
    match = _KEY_RE.search(response.text)
    assert match is not None
    return match.group(0)


def _prefix_of(full_key: str) -> str:
    return full_key.split("_")[1]


def _login(client: TestClient, password: str):
    return client.post(
        "/admin/login",
        data={"password": password},
        headers=_EN,
        follow_redirects=False,
    )


def test_admin_requires_login(client: TestClient) -> None:
    response = client.get("/admin", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/login"


def test_login_rejects_wrong_password(client: TestClient) -> None:
    response = _login(client, "wrong")

    assert response.status_code == 200
    assert "Invalid password" in response.text
    page = client.get("/admin", follow_redirects=False)
    assert page.status_code == 303


def test_login_is_rate_limited_after_five_failures(client: TestClient) -> None:
    for _ in range(5):
        assert _login(client, "wrong").status_code == 200

    blocked = _login(client, "wrong")

    assert blocked.status_code == 429
    assert "Too many attempts" in blocked.text


def test_login_sets_session_and_admin_lists_keys(client: TestClient) -> None:
    response = _login(client, "test-admin")

    assert response.status_code == 303
    assert response.headers["location"] == "/admin"
    assert "admin_session" in response.headers["set-cookie"]

    page = client.get("/admin", headers=_EN)
    assert page.status_code == 200
    assert "API keys" in page.text


def test_logout_clears_session(client: TestClient) -> None:
    _login(client, "test-admin")

    response = client.post("/admin/logout", headers=_EN, follow_redirects=False)

    assert response.status_code == 303
    assert client.get("/admin", follow_redirects=False).status_code == 303


def test_login_page_reports_missing_admin_password(tmp_path) -> None:
    app = create_app(
        Settings(
            database_url=f"sqlite:///{(tmp_path / 'unconfigured.db').as_posix()}",
            secret_key="test-secret",
            admin_password=None,
        )
    )
    response = TestClient(app).get("/admin/login", headers=_EN)

    assert "Admin is not configured." in response.text


def _create_key(client: TestClient, name: str = "Alice"):
    return client.post(
        "/admin/keys", data={"name": name}, headers=_EN, follow_redirects=False
    )


def test_create_key_reveals_secret_only_once(client: TestClient) -> None:
    _login(client, "test-admin")

    created = _create_key(client, "Alice")

    assert created.status_code == 200
    full_key = _extract_key(created)
    prefix = _prefix_of(full_key)

    listed = client.get("/admin", headers=_EN)
    assert prefix in listed.text
    assert full_key not in listed.text


def test_create_key_requires_a_name(client: TestClient) -> None:
    _login(client, "test-admin")

    response = _create_key(client, "   ")

    assert response.status_code == 422
    assert "Name is required." in response.text


def test_revoke_key_marks_it_revoked(client: TestClient) -> None:
    _login(client, "test-admin")
    created = _create_key(client, "Bob")
    prefix = _prefix_of(_extract_key(created))

    page = client.get("/admin", headers=_EN)
    revoke_match = re.search(r"/admin/keys/(\d+)/revoke", page.text)
    assert revoke_match is not None

    response = client.post(
        f"/admin/keys/{revoke_match.group(1)}/revoke",
        headers=_EN,
        follow_redirects=False,
    )

    assert response.status_code == 303
    listed = client.get("/admin", headers=_EN)
    assert prefix in listed.text
    assert "Revoked" in listed.text


def test_api_requires_an_api_key(client: TestClient) -> None:
    missing = client.get("/api/v1/me")
    invalid = client.get(
        "/api/v1/me", headers={"X-API-Key": "cf_00000000_" + "0" * 32}
    )

    assert missing.status_code == 401
    assert missing.json()["error"]["code"] == "missing_api_key"
    assert invalid.status_code == 401
    assert invalid.json()["error"]["code"] == "invalid_api_key"


def test_valid_api_key_identifies_holder_and_counts_calls(
    client: TestClient,
) -> None:
    _login(client, "test-admin")
    created = _create_key(client, "Alice")
    full_key = _extract_key(created)

    first = client.get("/api/v1/me", headers={"X-API-Key": full_key})
    second = client.get("/api/v1/me", headers={"X-API-Key": full_key})

    assert first.status_code == 200
    assert first.json()["name"] == "Alice"
    assert first.json()["calls"] == 1
    assert second.json()["calls"] == 2
    page = client.get("/admin", headers=_EN)
    assert "Never" not in page.text


def test_revoked_key_is_rejected(client: TestClient) -> None:
    _login(client, "test-admin")
    created = _create_key(client, "Bob")
    full_key = _extract_key(created)
    page = client.get("/admin", headers=_EN)
    revoke_match = re.search(r"/admin/keys/(\d+)/revoke", page.text)
    assert revoke_match is not None
    client.post(
        f"/admin/keys/{revoke_match.group(1)}/revoke",
        headers=_EN,
        follow_redirects=False,
    )

    response = client.get("/api/v1/me", headers={"X-API-Key": full_key})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "api_key_revoked"


def test_login_page_renders_in_german(client: TestClient) -> None:
    response = client.get("/admin/login", headers={"Accept-Language": "de"})

    assert "Anmelden" in response.text
    assert "Passwort" in response.text


def test_secret_key_required_when_admin_enabled(tmp_path) -> None:
    with pytest.raises(RuntimeError):
        create_app(
            Settings(
                database_url=f"sqlite:///{(tmp_path / 'insecure.db').as_posix()}",
                secret_key=DEFAULT_SECRET_KEY,
                admin_password="secret",
            )
        )
