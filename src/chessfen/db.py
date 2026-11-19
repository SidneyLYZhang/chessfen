from __future__ import annotations

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase

from chessfen.settings import ensure_sqlite_directory


class Base(DeclarativeBase):
    pass


def build_engine(database_url: str) -> Engine:
    ensure_sqlite_directory(database_url)
    return create_engine(database_url)
