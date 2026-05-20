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
        "ALTER TABLE videos ADD COLUMN IF NOT EXISTS last_heartbeat_at TIMESTAMP",
        "CREATE INDEX IF NOT EXISTS ix_videos_last_heartbeat_at ON videos(last_heartbeat_at)",
        "ALTER TABLE projects ADD COLUMN IF NOT EXISTS last_exported_at TIMESTAMP",
        "ALTER TABLE videos ADD COLUMN IF NOT EXISTS tus_upload_id VARCHAR(64)",
        "ALTER TABLE videos ADD COLUMN IF NOT EXISTS scene_frames JSON DEFAULT '[]'",
        "ALTER TABLE videos ADD COLUMN IF NOT EXISTS source VARCHAR(32) DEFAULT 'upload'",
        "ALTER TABLE videos ADD COLUMN IF NOT EXISTS local_source_path VARCHAR(1024)",
        # Lines now belong to a specific video. Make project_id nullable so
        # legacy rows survive the migration; new rows must carry video_id.
        "ALTER TABLE counting_lines ALTER COLUMN project_id DROP NOT NULL",
        "ALTER TABLE counting_lines ADD COLUMN IF NOT EXISTS video_id UUID REFERENCES videos(id) ON DELETE CASCADE",
        "CREATE INDEX IF NOT EXISTS ix_counting_lines_video_id ON counting_lines(video_id)",
        # Migration ledger — used to gate one-shot data migrations.
        """CREATE TABLE IF NOT EXISTS _migrations (
            name VARCHAR PRIMARY KEY,
            applied_at TIMESTAMP DEFAULT NOW()
        )""",
        """CREATE TABLE IF NOT EXISTS tus_uploads (
            id VARCHAR PRIMARY KEY,
            project_id VARCHAR NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            video_id VARCHAR REFERENCES videos(id) ON DELETE SET NULL,
            filename VARCHAR(512) NOT NULL,
            upload_length BIGINT NOT NULL,
            created_at TIMESTAMP DEFAULT NOW()
        )""",
    ]
    with engine.begin() as conn:
        for stmt in stmts:
            conn.execute(text(stmt))
        # One-shot cleanup: legacy project-scoped lines (no video_id) are
        # discarded for the public-test deploy — the user re-draws per video.
        already = conn.execute(
            text("SELECT 1 FROM _migrations WHERE name = :n"),
            {"n": "drop_orphan_project_lines_v1"},
        ).first()
        if not already:
            conn.execute(text("DELETE FROM counting_lines WHERE video_id IS NULL"))
            conn.execute(
                text("INSERT INTO _migrations(name) VALUES (:n)"),
                {"n": "drop_orphan_project_lines_v1"},
            )
