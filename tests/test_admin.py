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


def _revoke_newest(client: TestClient) -> None:
    page = client.get("/admin", headers=_EN)
    match = re.search(r"/admin/keys/(\d+)/revoke", page.text)
    assert match is not None
    response = client.post(
        f"/admin/keys/{match.group(1)}/revoke",
        headers=_EN,
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_create_key_reveals_secret_only_once(client: TestClient) -> None:
    _login(client, "test-admin")

    created = _create_key(client, "Alice")

    assert created.status_code == 200
    full_key = _extract_key(created)
    prefix = _prefix_of(full_key)
    assert 'id="new-key-text"' in created.text
    assert 'data-copy-target="new-key-text"' in created.text

    listed = client.get("/admin", headers=_EN)
    assert prefix in listed.text
    assert full_key not in listed.text


def test_admin_stats_count_total_active_and_revoked(client: TestClient) -> None:
    _login(client, "test-admin")
    _create_key(client, "Alice")
    _create_key(client, "Bob")
    _revoke_newest(client)

    page = client.get("/admin", headers=_EN)

    assert '<strong id="stat-total">2</strong>' in page.text
    assert '<strong id="stat-active">1</strong>' in page.text
    assert '<strong id="stat-revoked">1</strong>' in page.text


def test_admin_searches_by_name_and_prefix(client: TestClient) -> None:
    _login(client, "test-admin")
    alice = _create_key(client, "Alice")
    _create_key(client, "Bob")
    alice_prefix = _prefix_of(_extract_key(alice))

    by_name = client.get("/admin?q=ali", headers=_EN)
    assert "Alice" in by_name.text
    assert "Bob" not in by_name.text
    assert 'value="ali"' in by_name.text

    by_prefix = client.get(f"/admin?q={alice_prefix}", headers=_EN)
    assert alice_prefix in by_prefix.text
    assert "Bob" not in by_prefix.text


def test_admin_filters_by_status(client: TestClient) -> None:
    _login(client, "test-admin")
    _create_key(client, "Alice")
    _create_key(client, "Bob")
    _revoke_newest(client)

    active = client.get("/admin?status=active", headers=_EN)
    assert "Alice" in active.text
    assert "Bob" not in active.text
    assert '<option value="active" selected>' in active.text

    revoked = client.get("/admin?status=revoked", headers=_EN)
    assert "Bob" in revoked.text
    assert "Alice" not in revoked.text
    assert '<option value="revoked" selected>' in revoked.text

    combined = client.get("/admin?q=ali&status=revoked", headers=_EN)
    assert "No keys match these filters." in combined.text
    assert "Clear filters" in combined.text


def test_admin_empty_search_state(client: TestClient) -> None:
    _login(client, "test-admin")
    _create_key(client, "Alice")

    page = client.get("/admin?q=zzz", headers=_EN)

    assert "No keys match these filters." in page.text
    assert "Clear filters" in page.text
    empty = page.text.split('class="admin-empty"', 1)[1].split(
        "</section>", 1
    )[0]
    assert 'href="/admin"' in empty


def test_admin_search_treats_wildcards_literally(client: TestClient) -> None:
    _login(client, "test-admin")
    _create_key(client, "Alice")

    page = client.get("/admin?q=%25", headers=_EN)

    assert "No keys match these filters." in page.text


def test_admin_invalid_status_falls_back_to_all(client: TestClient) -> None:
    _login(client, "test-admin")
    _create_key(client, "Alice")

    page = client.get("/admin?status=nope", headers=_EN)

    assert "Alice" in page.text
    assert '<option value="all" selected>' in page.text


def test_create_key_preserves_the_status_filter(client: TestClient) -> None:
    _login(client, "test-admin")

    page = client.post(
        "/admin/keys",
        data={"name": "Alice", "status": "active"},
        headers=_EN,
    )

    assert page.status_code == 200
    assert '<option value="active" selected>' in page.text
    assert 'name="status" value="active"' in page.text


def test_revoke_preserves_the_status_filter(client: TestClient) -> None:
    _login(client, "test-admin")
    _create_key(client, "Alice")

    page = client.get("/admin?status=active", headers=_EN)
    match = re.search(
        r'action="(/admin/keys/\d+/revoke\?status=active)"', page.text
    )
    assert match is not None

    response = client.post(
        match.group(1), headers=_EN, follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/admin?status=active"


def test_revoke_requires_native_confirmation(client: TestClient) -> None:
    _login(client, "test-admin")
    _create_key(client, "Alice")

    page = client.get("/admin", headers=_EN)

    assert 'class="revoke-confirm"' in page.text
    assert "<details" in page.text
    assert "Confirm revoke" in page.text
    assert re.search(r"/admin/keys/\d+/revoke", page.text)


def test_login_page_has_brand_panel(client: TestClient) -> None:
    page = client.get("/admin/login", headers=_EN)

    assert 'class="login-brand"' in page.text
    assert "/static/brand/mark-reverse.svg" in page.text
    assert 'type="password"' in page.text
    assert 'name="password"' in page.text


def test_admin_page_is_localized(client: TestClient) -> None:
    _login(client, "test-admin")

    page = client.get("/admin", headers={"Accept-Language": "de"})

    assert "API-Schlüssel" in page.text
    assert "Gesamt" in page.text
    assert "Suchen" in page.text


def test_unknown_api_path_stays_json(client: TestClient, api_key: str) -> None:
    response = client.get(
        "/api/v1/nope", headers={"X-API-Key": api_key}
    )

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert "Page not found" not in response.text


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
                secret_key=None,
                admin_password="secret",
            )
        )


def test_explicit_secret_key_value_is_accepted(tmp_path) -> None:
    # An explicitly configured value is honoured even when it equals the
    # fallback constant used for the (admin-disabled) default app.
    app = create_app(
        Settings(
            database_url=f"sqlite:///{(tmp_path / 'explicit.db').as_posix()}",
            secret_key=DEFAULT_SECRET_KEY,
            admin_password="secret",
        )
    )

    assert app is not None
