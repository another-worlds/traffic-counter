from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, declarative_base, Session
from contextlib import contextmanager
from .config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def db_session() -> Session:
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db():
    """Create tables if they don't exist. For production, use Alembic."""
    from . import models  # noqa: F401
    Base.metadata.create_all(bind=engine)

    # Safe additive migrations for columns added after initial deployment.
    _safe_add_columns()


def _safe_add_columns():
    """Add new columns to existing tables without data loss (idempotent)."""
    stmts = [
        "ALTER TABLE videos ADD COLUMN IF NOT EXISTS size_bytes BIGINT",
        "ALTER TABLE videos ADD COLUMN IF NOT EXISTS progress_pct FLOAT DEFAULT 0.0",
        "ALTER TABLE videos ADD COLUMN IF NOT EXISTS started_analyzing_at TIMESTAMP",
        "ALTER TABLE projects ADD COLUMN IF NOT EXISTS last_exported_at TIMESTAMP",
    ]
    with engine.begin() as conn:
        for stmt in stmts:
            conn.execute(text(stmt))
