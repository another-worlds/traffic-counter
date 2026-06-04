"""SQLAlchemy engine/session wiring.

Mirrors the traffic-counter API's pattern (api/app/db.py): a single engine, a
sessionmaker, a declarative Base, and create_all() for MVP simplicity. New columns on
pre-existing tables are added idempotently by _safe_add_columns (create_all does not
ALTER existing tables). Swap for Alembic before evolving the schema in production.
"""
from __future__ import annotations

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import declarative_base, sessionmaker

from .config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
Base = declarative_base()

# Columns added to tables that may already exist from an earlier schema.
_NEW_COLUMNS = {
    "nodes": [("node_type_id", "VARCHAR")],
    "links": [("link_type_id", "VARCHAR"), ("v0_kmh", "FLOAT"), ("allowed_modes", "JSON")],
    "zones": [("zone_type_id", "VARCHAR"), ("population", "FLOAT"), ("workplaces", "FLOAT")],
    "od_matrices": [("kind", "VARCHAR"), ("mode_id", "VARCHAR"), ("demand_layer_id", "VARCHAR")],
    "demand_layers": [("prod_var", "VARCHAR"), ("attr_var", "VARCHAR"), ("trip_rate", "FLOAT")],
}


def _safe_add_columns() -> None:
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    with engine.begin() as conn:
        for table, cols in _NEW_COLUMNS.items():
            if table not in tables:
                continue
            have = {c["name"] for c in insp.get_columns(table)}
            for name, sqltype in cols:
                if name in have:
                    continue
                coltype = "TEXT" if (sqltype == "JSON" and engine.dialect.name == "sqlite") else sqltype
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {coltype}"))


def init_db() -> None:
    from . import models  # noqa: F401 — register models on Base.metadata

    Base.metadata.create_all(engine)
    _safe_add_columns()


def get_db():
    """FastAPI dependency: yields a session and always closes it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
