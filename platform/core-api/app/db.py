"""SQLAlchemy engine/session wiring for the core-api.

Greenfield: a single engine + sessionmaker + DeclarativeBase. `init_db` enables the
PostGIS extension and creates tables (GeoAlchemy2 emits the GiST spatial indexes as
part of `create_all`). Production should move to Alembic migrations — there is no
GeoJSON-in-JSON legacy to migrate from, so the schema starts clean and native.
"""
from __future__ import annotations

import time

from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


class Base(DeclarativeBase):
    pass


def init_db(retries: int = 15, delay: float = 1.5) -> None:
    """Enable PostGIS + create all tables/indexes. Retries while the DB warms up."""
    from . import models  # noqa: F401 — register models (and GeoAlchemy2 listeners) on Base

    last: Exception | None = None
    for _ in range(retries):
        try:
            with engine.begin() as conn:
                conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
            Base.metadata.create_all(engine)
            return
        except OperationalError as exc:  # DB not accepting connections yet
            last = exc
            time.sleep(delay)
    if last is not None:
        raise last


def get_db():
    """FastAPI dependency: yield a session, always close it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
