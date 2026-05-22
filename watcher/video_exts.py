"""Shared canonical list of video file extensions.

Kept in one place so the watcher, the API, and the verification script
all agree on what counts as a "video file" for auto-import purposes.
"""
from __future__ import annotations

VIDEO_EXTS: frozenset[str] = frozenset({
    ".mp4", ".mov", ".avi", ".mkv", ".ts",
    ".m4v", ".wmv", ".mts", ".m2ts", ".webm",
})


def is_video_filename(name: str) -> bool:
    """Cheap suffix check — does not stat the filesystem."""
    dot = name.rfind(".")
    if dot < 0:
        return False
    return name[dot:].lower() in VIDEO_EXTS
