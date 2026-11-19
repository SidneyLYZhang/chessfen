from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.engine import make_url

DEFAULT_DATABASE_URL = "sqlite:///./data/chessfen.db"


@dataclass(frozen=True)
class Settings:
    database_url: str


def get_settings() -> Settings:
    return Settings(
        database_url=os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
    )


def ensure_sqlite_directory(database_url: str) -> None:
    """Create the parent directory for file-backed SQLite URLs."""
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite":
        return
    database = url.database
    if not database or database == ":memory:" or database.startswith("file:"):
        return
    Path(database).resolve().parent.mkdir(parents=True, exist_ok=True)
