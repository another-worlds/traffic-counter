"""
Worker storage backend — local FS or GCS.
Mirrors the API's storage.py contract so keys stay consistent.
"""
from __future__ import annotations
import os
import shutil
import io
from pathlib import Path
from typing import BinaryIO


class Storage:
    def upload_file(self, key: str, local_path: str) -> None: ...
    def download_to(self, key: str, local_path: str) -> None: ...
    def exists(self, key: str) -> bool: ...


class LocalStorage(Storage):
    def __init__(self, root: str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        p = self.root / key
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def upload_file(self, key, local_path):
        shutil.copy(local_path, self._path(key))

    def download_to(self, key, local_path):
        Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(self._path(key), local_path)

    def exists(self, key):
        return self._path(key).exists()


class GCSStorage(Storage):
    def __init__(self, bucket_name: str):
        from google.cloud import storage as gcs
        self.client = gcs.Client()
        self.bucket = self.client.bucket(bucket_name)

    def upload_file(self, key, local_path):
        self.bucket.blob(key).upload_from_filename(local_path)

    def download_to(self, key, local_path):
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        self.bucket.blob(key).download_to_filename(local_path)

    def exists(self, key):
        return self.bucket.blob(key).exists(self.client)


def get_storage() -> Storage:
    backend = os.environ.get("STORAGE_BACKEND", "local")
    if backend == "gcs":
        bucket = os.environ["GCS_BUCKET"]
        return GCSStorage(bucket)
    return LocalStorage(os.environ.get("LOCAL_STORAGE_ROOT", "/data"))


def key_video(project_id: str, video_id: str, filename: str) -> str:
    ext = os.path.splitext(filename)[1].lower() or ".mp4"
    return f"projects/{project_id}/videos/{video_id}/source{ext}"

def key_tracks(project_id: str, video_id: str) -> str:
    return f"projects/{project_id}/videos/{video_id}/tracks.parquet"

def key_scene_frame(project_id: str, video_id: str, n: int) -> str:
    return f"projects/{project_id}/videos/{video_id}/frames/{n}.jpg"

def key_frame(project_id: str, video_id: str) -> str:
    # Backward-compat alias — always points at scene 0 after re-analysis.
    return key_scene_frame(project_id, video_id, 0)

def key_trajectories(project_id: str, video_id: str) -> str:
    return f"projects/{project_id}/videos/{video_id}/trajectories.png"
