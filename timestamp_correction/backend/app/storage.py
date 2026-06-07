"""Shared storage helpers — mirrors traffic-counter key layout."""
from __future__ import annotations

import io
import json
import os
import shutil
from pathlib import Path
from typing import Any, BinaryIO

from .config import settings


class Storage:
    def upload_file(self, key: str, local_path: str) -> None: ...
    def download_to(self, key: str, local_path: str) -> None: ...
    def exists(self, key: str) -> bool: ...
    def delete(self, key: str) -> None: ...
    def open_read(self, key: str) -> BinaryIO: ...
    def write_json(self, key: str, obj: Any) -> None: ...
    def read_json(self, key: str) -> Any: ...


class LocalStorage(Storage):
    def __init__(self, root: str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        p = self.root / key
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def upload_file(self, key: str, local_path: str) -> None:
        shutil.copy(local_path, self._path(key))

    def download_to(self, key: str, local_path: str) -> None:
        Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(self._path(key), local_path)

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def delete(self, key: str) -> None:
        p = self._path(key)
        if p.exists():
            p.unlink()

    def open_read(self, key: str) -> BinaryIO:
        return open(self._path(key), "rb")

    def write_json(self, key: str, obj: Any) -> None:
        self._path(key).write_text(json.dumps(obj, indent=2), encoding="utf-8")

    def read_json(self, key: str) -> Any:
        return json.loads(self._path(key).read_text(encoding="utf-8"))


class GCSStorage(Storage):
    def __init__(self, bucket_name: str):
        from google.cloud import storage as gcs
        self.client = gcs.Client()
        self.bucket = self.client.bucket(bucket_name)

    def upload_file(self, key: str, local_path: str) -> None:
        self.bucket.blob(key).upload_from_filename(local_path)

    def download_to(self, key: str, local_path: str) -> None:
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        self.bucket.blob(key).download_to_filename(local_path)

    def exists(self, key: str) -> bool:
        return self.bucket.blob(key).exists(self.client)

    def delete(self, key: str) -> None:
        self.bucket.blob(key).delete()

    def open_read(self, key: str) -> BinaryIO:
        data = self.bucket.blob(key).download_as_bytes()
        return io.BytesIO(data)

    def write_json(self, key: str, obj: Any) -> None:
        self.bucket.blob(key).upload_from_string(
            json.dumps(obj, indent=2), content_type="application/json"
        )

    def read_json(self, key: str) -> Any:
        return json.loads(self.bucket.blob(key).download_as_text())


def get_storage() -> Storage:
    if settings.storage_backend == "gcs":
        return GCSStorage(settings.gcs_bucket)
    return LocalStorage(settings.local_storage_root)


def key_timestamp_region(project_id: str, video_id: str) -> str:
    return f"projects/{project_id}/videos/{video_id}/timestamp_region.json"


def key_timestamp_timeline(project_id: str, video_id: str) -> str:
    return f"projects/{project_id}/videos/{video_id}/timestamp_timeline.parquet"


def key_timestamp_gaps(project_id: str, video_id: str) -> str:
    return f"projects/{project_id}/videos/{video_id}/timestamp_gaps.json"


def key_timestamp_sync_map(project_id: str, video_id: str) -> str:
    return f"projects/{project_id}/videos/{video_id}/timestamp_sync_map.json"


def key_timestamp_status(project_id: str, video_id: str) -> str:
    return f"projects/{project_id}/videos/{video_id}/timestamp_status.json"


def key_timestamp_index(video_id: str) -> str:
    """Lightweight lookup so status polls don't need the counter API."""
    return f"timestamp_index/{video_id}.json"


def key_timestamp_region_preview(project_id: str, video_id: str) -> str:
    """JPEG crop of the detected OSD region (reference frame) for verification."""
    return f"projects/{project_id}/videos/{video_id}/timestamp_region.jpg"


def key_tracks(project_id: str, video_id: str) -> str:
    return f"projects/{project_id}/videos/{video_id}/tracks.parquet"


def key_tracks_segment(project_id: str, video_id: str, segment_idx: int) -> str:
    return f"projects/{project_id}/videos/{video_id}/tracks_segment_{segment_idx:04d}.parquet"