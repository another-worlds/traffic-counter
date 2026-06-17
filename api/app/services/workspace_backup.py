"""Build a portable ZIP backup of watched-folder workspace data.

Includes Postgres rows (projects, videos, lines, segments, timestamp scans) and
all derived artifacts under ``projects/{project_id}/`` in shared storage.

Source video files for local-folder imports are *not* copied — they remain on
the Yandex mount and are referenced by ``local_source_path`` in the DB.
"""
from __future__ import annotations

import json
import os
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set

from sqlalchemy.orm import Session

from ..config import settings
from ..models import CountingLine, Project, TimestampScan, Video, VideoSegment
from .storage import get_storage

BACKUP_VERSION = 1


class BackupCancelled(Exception):
    """Raised when a backup job is cancelled mid-build."""


def _serialize_value(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return value


def _serialize_row(row: Any) -> Dict[str, Any]:
    return {
        col.name: _serialize_value(getattr(row, col.name))
        for col in row.__table__.columns
    }


def _folder_key_for_video(video: Video) -> str:
    local_path = video.local_source_path or ""
    if local_path:
        parent = Path(local_path).parent
        return str(parent) if str(parent) else "(root)"
    return "(unknown)"


def _videos_for_scope(
    db: Session,
    *,
    scope: str,
    folder: Optional[str],
) -> List[Video]:
    q = db.query(Video).filter(Video.source == "local-folder")
    videos = q.order_by(Video.created_at.asc()).all()
    if scope == "all":
        return videos
    if scope != "folder":
        raise ValueError(f"unsupported backup scope: {scope}")
    if not folder:
        raise ValueError("folder is required when scope=folder")
    return [v for v in videos if _folder_key_for_video(v) == folder]


def _project_ids(videos: Sequence[Video]) -> List[str]:
    return sorted({v.project_id for v in videos})


def _skip_storage_key(key: str, local_folder_video_ids: Set[str]) -> bool:
    """Skip copied source media for watcher-imported videos."""
    parts = key.split("/")
    if len(parts) < 4 or parts[0] != "projects" or parts[2] != "videos":
        return False
    video_id = parts[3]
    if video_id not in local_folder_video_ids:
        return False
    name = parts[-1]
    return name.startswith("source.")


def _iter_local_keys(prefix: str) -> Iterable[str]:
    root = Path(settings.local_storage_root)
    base = root / prefix
    if not base.is_dir():
        return
    for path in base.rglob("*"):
        if path.is_file():
            yield str(path.relative_to(root))


def _iter_storage_keys(project_ids: Sequence[str]) -> Iterable[str]:
    if settings.storage_backend != "local":
        for project_id in project_ids:
            yield from _iter_local_keys(f"projects/{project_id}/")
        return
    root = Path(settings.local_storage_root)
    for project_id in project_ids:
        base = root / "projects" / project_id
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if path.is_file():
                yield str(path.relative_to(root))


def _check_cancel(cancel_cb: Callable[[], bool] | None) -> None:
    if cancel_cb and cancel_cb():
        raise BackupCancelled()


def build_workspace_backup_zip(
    db: Session,
    *,
    scope: str,
    folder: Optional[str] = None,
    cancel_cb: Callable[[], bool] | None = None,
    progress_cb: Callable[[int, int], None] | None = None,
) -> tuple[str, Dict[str, Any]]:
    """Write backup ZIP to a temp file; caller deletes the path after streaming."""
    _check_cancel(cancel_cb)
    videos = _videos_for_scope(db, scope=scope, folder=folder)
    if not videos:
        raise ValueError("no watched-folder videos match the requested backup scope")

    video_ids = [v.id for v in videos]
    project_ids = _project_ids(videos)
    local_folder_ids = {v.id for v in videos if v.local_source_path}

    projects = (
        db.query(Project)
        .filter(Project.id.in_(project_ids))
        .order_by(Project.name.asc())
        .all()
    )
    lines = (
        db.query(CountingLine)
        .filter(CountingLine.video_id.in_(video_ids))
        .order_by(CountingLine.created_at.asc())
        .all()
    )
    segments = (
        db.query(VideoSegment)
        .filter(VideoSegment.video_id.in_(video_ids))
        .order_by(VideoSegment.video_id.asc(), VideoSegment.segment_idx.asc())
        .all()
    )
    scans = (
        db.query(TimestampScan)
        .filter(TimestampScan.video_id.in_(video_ids))
        .order_by(TimestampScan.updated_at.asc())
        .all()
    )

    manifest: Dict[str, Any] = {
        "version": BACKUP_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "scope": {"type": scope, "folder": folder},
        "project_ids": project_ids,
        "video_count": len(videos),
        "notes": (
            "Database rows and derived artifacts only. "
            "Local-folder source videos are not included; restore requires the same Yandex paths."
        ),
    }

    db_payload = {
        "projects": [_serialize_row(p) for p in projects],
        "videos": [_serialize_row(v) for v in videos],
        "counting_lines": [_serialize_row(ln) for ln in lines],
        "video_segments": [_serialize_row(seg) for seg in segments],
        "timestamp_scans": [_serialize_row(sc) for sc in scans],
    }

    storage = get_storage()
    included_files: List[str] = []
    skipped_sources: List[str] = []

    seen_keys: Set[str] = set()
    storage_keys: List[str] = []
    for key in _iter_storage_keys(project_ids):
        if key in seen_keys:
            continue
        seen_keys.add(key)
        if _skip_storage_key(key, local_folder_ids):
            skipped_sources.append(key)
            continue
        if not storage.exists(key):
            continue
        storage_keys.append(key)
    total_storage = len(storage_keys)

    fd, tmp_path = tempfile.mkstemp(suffix=".zip", prefix="workspace-backup-")
    os.close(fd)

    try:
        with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            _check_cancel(cancel_cb)
            zf.writestr(
                "manifest.json",
                json.dumps(manifest, indent=2, ensure_ascii=False),
            )
            zf.writestr(
                "database/export.json",
                json.dumps(db_payload, indent=2, ensure_ascii=False),
            )

            for idx, key in enumerate(storage_keys, start=1):
                _check_cancel(cancel_cb)
                if progress_cb:
                    progress_cb(idx, total_storage)
                with storage.open_read(key) as fp:
                    zf.writestr(f"storage/{key}", fp.read())
                included_files.append(key)

        manifest["storage_file_count"] = len(included_files)
        manifest["skipped_source_videos"] = skipped_sources

        with zipfile.ZipFile(tmp_path, "a", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(
                "manifest.json",
                json.dumps(manifest, indent=2, ensure_ascii=False),
            )

        summary = {
            "video_count": len(videos),
            "project_count": len(project_ids),
            "storage_file_count": len(included_files),
            "skipped_source_videos": len(skipped_sources),
            "scope": manifest["scope"],
        }
        return tmp_path, summary
    except BackupCancelled:
        Path(tmp_path).unlink(missing_ok=True)
        raise
    except Exception:
        Path(tmp_path).unlink(missing_ok=True)
        raise