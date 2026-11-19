from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from chessfen.settings import ensure_sqlite_directory, get_settings

_settings = get_settings()
ensure_sqlite_directory(_settings.database_url)

engine = create_engine(_settings.database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass
