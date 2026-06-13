"""Test fixtures.

These tests exercise **real PostGIS** behaviour (native geometry round-trip + GiST KNN),
so they require a PostGIS database — point ``DATABASE_URL`` at one (the docker-compose
``model-db`` service, reachable as ``model-db:5432`` inside the container or
``localhost:5434`` from the host). They are skipped if no database is reachable.
"""
from __future__ import annotations

import os

import pytest

# Inside the core-api container DATABASE_URL is already set to the model-db service.
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://model:model@localhost:5434/model")


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient
    from sqlalchemy import text

    from app.db import engine
    from app.main import app

    # Skip cleanly if no PostGIS is reachable.
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - environment-dependent
        pytest.skip(f"no database reachable for tests: {exc}")

    with TestClient(app) as c:  # triggers lifespan -> init_db (PostGIS ext + tables + GiST)
        yield c
