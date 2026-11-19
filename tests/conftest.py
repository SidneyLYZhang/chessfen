from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from chessfen.app import create_app
from chessfen.settings import Settings


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    database_url = f"sqlite:///{(tmp_path / 'test.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    command.upgrade(Config("alembic.ini"), "head")
    app = create_app(
        Settings(
            database_url=database_url,
            secret_key="test-secret",
            admin_password="test-admin",
        )
    )
    with TestClient(app) as test_client:
        yield test_client
