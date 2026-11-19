from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from chessfen.app import create_app
from chessfen.settings import Settings

_KEY_RE = re.compile(r"cf_[0-9a-f]{8}_[0-9a-f]{32}")


@pytest.fixture
def client_factory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Callable[..., TestClient]:
    created = 0

    def make(recognizer: Any | None = None) -> TestClient:
        nonlocal created
        created += 1
        database_url = f"sqlite:///{(tmp_path / f'test-{created}.db').as_posix()}"
        monkeypatch.setenv("DATABASE_URL", database_url)
        command.upgrade(Config("alembic.ini"), "head")
        app = create_app(
            Settings(
                database_url=database_url,
                secret_key="test-secret",
                admin_password="test-admin",
            )
        )
        if recognizer is not None:
            app.state.recognizer = recognizer
        return TestClient(app)

    return make


@pytest.fixture
def client(client_factory: Callable[..., TestClient]) -> Iterator[TestClient]:
    with client_factory() as test_client:
        yield test_client


@pytest.fixture
def admin_client(client: TestClient) -> TestClient:
    client.post(
        "/admin/login",
        data={"password": "test-admin"},
        headers={"Accept-Language": "en"},
        follow_redirects=False,
    )
    return client


@pytest.fixture
def api_key(admin_client: TestClient) -> str:
    response = admin_client.post(
        "/admin/keys",
        data={"name": "render-tests"},
        headers={"Accept-Language": "en"},
        follow_redirects=False,
    )
    match = _KEY_RE.search(response.text)
    assert match is not None
    return match.group(0)
