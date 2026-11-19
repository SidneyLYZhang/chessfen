from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.engine import make_url

DEFAULT_DATABASE_URL = "sqlite:///./data/chessfen.db"
DEFAULT_SECRET_KEY = "dev-secret-change-me"
DEFAULT_RECOGNITION_MODEL_PATH = "models/yolo26m-finetuned.onnx"


@dataclass(frozen=True)
class Settings:
    database_url: str
    secret_key: str | None
    admin_password: str | None
    recognition_model_path: str = DEFAULT_RECOGNITION_MODEL_PATH


def get_settings() -> Settings:
    return Settings(
        database_url=os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL),
        secret_key=os.environ.get("SECRET_KEY") or None,
        admin_password=os.environ.get("ADMIN_PASSWORD") or None,
        recognition_model_path=os.environ.get(
            "RECOGNITION_MODEL_PATH", DEFAULT_RECOGNITION_MODEL_PATH
        ),
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
