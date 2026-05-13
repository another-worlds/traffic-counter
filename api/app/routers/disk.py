"""Yandex.Disk file-browser and direct-import endpoints.

All path arguments are relative to settings.yadisk_root.  Every resolved path
is checked to remain inside that root to prevent directory traversal.
"""
from __future__ import annotations

import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from fastapi import Depends

from ..config import settings
from ..db import get_db
from ..models import Project, Video
from ..schemas import VideoOut
from ..services.storage import get_storage, key_video

router = APIRouter(tags=["disk"])

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".ts", ".m4v"}


# ── helpers ──────────────────────────────────────────────────────────────────

def _yadisk_root() -> Path:
    if not settings.yadisk_root:
        raise HTTPException(503, "Yandex.Disk integration is not configured (YADISK_ROOT not set)")
    root = Path(settings.yadisk_root).resolve()
    if not root.is_dir():
        raise HTTPException(503, f"Yandex.Disk root {root} does not exist or is not a directory")
    return root


def _safe_resolve(root: Path, rel: str) -> Path:
    """Resolve *rel* inside *root* and raise 400 if the result escapes root."""
    # Strip leading slashes so Path joining works predictably
    rel = rel.lstrip("/\\")
    resolved = (root / rel).resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        raise HTTPException(400, "Path escapes the Yandex.Disk root")
    return resolved


# ── schemas ───────────────────────────────────────────────────────────────────

class DiskEntry(BaseModel):
    name: str
    is_dir: bool
    is_video: bool
    size: Optional[int]
    modified: Optional[datetime]


class BrowseResponse(BaseModel):
    path: str          # canonical relative path shown to the client
    entries: List[DiskEntry]


class ImportRequest(BaseModel):
    disk_path: str     # relative to yadisk_root


# ── endpoints ─────────────────────────────────────────────────────────────────

@router.get("/disk/browse", response_model=BrowseResponse)
def browse_disk(path: str = ""):
    """List files and folders inside the Yandex.Disk root."""
    root = _yadisk_root()
    target = _safe_resolve(root, path)

    if not target.is_dir():
        raise HTTPException(404, "Not a directory")

    entries: List[DiskEntry] = []
    for child in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        if child.name.startswith("."):
            continue
        stat = child.stat()
        entries.append(DiskEntry(
            name=child.name,
            is_dir=child.is_dir(),
            is_video=child.is_file() and child.suffix.lower() in VIDEO_EXTENSIONS,
            size=stat.st_size if child.is_file() else None,
            modified=datetime.fromtimestamp(stat.st_mtime),
        ))

    # Return the path relative to root for the UI
    rel_path = str(target.relative_to(root)).replace("\\", "/")
    if rel_path == ".":
        rel_path = ""
    return BrowseResponse(path=rel_path, entries=entries)


@router.post(
    "/projects/{project_id}/videos/from-disk",
    response_model=VideoOut,
    status_code=201,
)
def import_from_disk(
    project_id: str,
    body: ImportRequest,
    db: Session = Depends(get_db),
):
    """Register a file from Yandex.Disk as a video in a project.

    The file is copied (or hard-linked when possible) into the storage backend,
    then a Video record is created with status='uploaded'.
    """
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "project not found")

    root = _yadisk_root()
    src = _safe_resolve(root, body.disk_path)

    if not src.is_file():
        raise HTTPException(404, "File not found on disk")
    if src.suffix.lower() not in VIDEO_EXTENSIONS:
        raise HTTPException(400, f"Not a supported video format: {src.suffix}")

    v = Video(
        project_id=project_id,
        filename=src.name,
        storage_path="",
        status="uploaded",
        size_bytes=src.stat().st_size,
    )
    db.add(v)
    db.flush()

    v.storage_path = key_video(project_id, v.id, v.filename)

    # Prefer hardlink (instant, zero extra disk) then fall back to copy
    storage = get_storage()
    dest = Path(settings.local_storage_root) / v.storage_path
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(src, dest)
    except OSError:
        shutil.copy2(src, dest)

    db.commit()
    db.refresh(v)
    return v
