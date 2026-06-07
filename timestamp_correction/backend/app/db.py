"""Optional Postgres persistence for timestamp scan results."""
from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, Generator, Optional

from sqlalchemy import JSON, Column, DateTime, String, Text, create_engine, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from .config import settings

log = logging.getLogger("timestamp_correction.db")

Base = declarative_base()


class TimestampScanRow(Base):
    __tablename__ = "timestamp_scans"

    video_id = Column(UUID(as_uuid=False), primary_key=True)
    status = Column(String(32), nullable=False, default="pending")
    progress = Column(JSON, nullable=True)
    region = Column(JSON, nullable=True)
    gaps = Column(JSON, nullable=True)
    stats = Column(JSON, nullable=True)
    timeline_summary = Column(JSON, nullable=True)
    timeline_viz = Column(JSON, nullable=True)
    error_message = Column(Text, nullable=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


_engine = None
_SessionLocal = None


def db_enabled() -> bool:
    return bool(settings.database_url)


def _ensure_engine():
    global _engine, _SessionLocal
    if _engine is not None:
        return
    if not settings.database_url:
        return
    _engine = create_engine(settings.database_url, pool_pre_ping=True, future=True)
    _SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False, future=True)
    with _engine.begin() as conn:
        conn.execute(text(
            """CREATE TABLE IF NOT EXISTS timestamp_scans (
                video_id UUID PRIMARY KEY,
                status VARCHAR(32) NOT NULL DEFAULT 'pending',
                progress JSON,
                region JSON,
                gaps JSON,
                stats JSON,
                timeline_summary JSON,
                timeline_viz JSON,
                error_message TEXT,
                started_at TIMESTAMP,
                completed_at TIMESTAMP,
                updated_at TIMESTAMP DEFAULT NOW()
            )"""
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_timestamp_scans_status ON timestamp_scans(status)"
        ))


@contextmanager
def db_session() -> Generator[Session, None, None]:
    _ensure_engine()
    if _SessionLocal is None:
        raise RuntimeError("database not configured")
    db = _SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def upsert_scan(video_id: str, payload: Dict[str, Any]) -> None:
    if not db_enabled():
        return
    try:
        _ensure_engine()
        with db_session() as db:
            row = db.get(TimestampScanRow, video_id)
            if row is None:
                row = TimestampScanRow(video_id=video_id, status="pending")
                db.add(row)
            for key in (
                "status", "progress", "region", "gaps", "stats",
                "timeline_summary", "timeline_viz", "error_message",
            ):
                if key in payload:
                    setattr(row, key, payload[key])
            if "started_at" in payload:
                row.started_at = _parse_dt(payload.get("started_at"))
            if "completed_at" in payload:
                row.completed_at = _parse_dt(payload.get("completed_at"))
            row.updated_at = datetime.utcnow()
    except Exception:
        log.exception("failed to upsert timestamp scan for %s", video_id)


def load_scans_for_videos(video_ids: list[str]) -> Dict[str, Dict[str, Any]]:
    """Bulk-load scan rows keyed by video_id."""
    if not db_enabled() or not video_ids:
        return {}
    try:
        _ensure_engine()
        with db_session() as db:
            rows = (
                db.query(TimestampScanRow)
                .filter(TimestampScanRow.video_id.in_(video_ids))
                .all()
            )
            out: Dict[str, Dict[str, Any]] = {}
            for row in rows:
                vid = str(row.video_id)
                out[vid] = {
                    "video_id": vid,
                    "status": row.status,
                    "progress": row.progress,
                    "region": row.region,
                    "gaps": row.gaps or [],
                    "stats": row.stats or {},
                    "timeline_summary": row.timeline_summary,
                    "timeline_viz": row.timeline_viz,
                    "error_message": row.error_message,
                    "started_at": row.started_at.isoformat() if row.started_at else None,
                    "completed_at": row.completed_at.isoformat() if row.completed_at else None,
                }
            return out
    except Exception:
        log.exception("failed to bulk-load timestamp scans")
        return {}


def delete_scan_row(video_id: str) -> None:
    if not db_enabled():
        return
    try:
        _ensure_engine()
        with db_session() as db:
            row = db.get(TimestampScanRow, video_id)
            if row is not None:
                db.delete(row)
    except Exception:
        log.exception("failed to delete timestamp scan for %s", video_id)


def load_scan(video_id: str) -> Optional[Dict[str, Any]]:
    if not db_enabled():
        return None
    try:
        _ensure_engine()
        with db_session() as db:
            row = db.get(TimestampScanRow, video_id)
            if row is None:
                return None
            return {
                "video_id": str(row.video_id),
                "status": row.status,
                "progress": row.progress,
                "region": row.region,
                "gaps": row.gaps or [],
                "stats": row.stats or {},
                "timeline_summary": row.timeline_summary,
                "timeline_viz": row.timeline_viz,
                "error_message": row.error_message,
                "started_at": row.started_at.isoformat() if row.started_at else None,
                "completed_at": row.completed_at.isoformat() if row.completed_at else None,
            }
    except Exception:
        log.exception("failed to load timestamp scan for %s", video_id)
        return None