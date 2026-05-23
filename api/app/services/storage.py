"""
Storage abstraction. Local FS for dev, GCS for prod.

Storage layout (same keys across backends):
    projects/{project_id}/videos/{video_id}/source.mp4
    projects/{project_id}/videos/{video_id}/tracks.parquet
    projects/{project_id}/videos/{video_id}/frame.jpg
    projects/{project_id}/videos/{video_id}/trajectories.png
    projects/{project_id}/exports/{export_id}.xlsx
"""
from __future__ import annotations
import os
import shutil
from pathlib import Path
from typing import BinaryIO
from datetime import timedelta

from ..config import settings


class Storage:
    def upload_stream(self, key: str, fp: BinaryIO) -> None: ...
    def upload_file(self, key: str, local_path: str) -> None: ...
    def download_to(self, key: str, local_path: str) -> None: ...
    def exists(self, key: str) -> bool: ...
    def stat(self, key: str) -> dict: ...
    def local_path(self, key: str) -> str | None: ...
    def public_url(self, key: str) -> str: ...
    def signed_url(self, key: str, expires_minutes: int = 60) -> str: ...
    def open_read(self, key: str) -> BinaryIO: ...
    def delete_prefix(self, prefix: str) -> None: ...
    def tus_part_path(self, upload_id: str) -> Path: ...
    def finalize_tus_upload(self, upload_id: str, key: str) -> None: ...
    def delete_tus_part(self, upload_id: str) -> None: ...


class LocalStorage(Storage):
    def __init__(self, root: str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        p = self.root / key
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def tus_part_path(self, upload_id: str) -> Path:
        p = self.root / "tmp" / "tus" / f"{upload_id}.part"
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def finalize_tus_upload(self, upload_id: str, key: str) -> None:
        """Move completed .part file to final storage key (same filesystem → atomic)."""
        src = self.tus_part_path(upload_id)
        dst = self._path(key)
        shutil.move(str(src), dst)

    def delete_tus_part(self, upload_id: str) -> None:
        p = self.tus_part_path(upload_id)
        if p.exists():
            p.unlink()

    def upload_stream(self, key, fp):
        with open(self._path(key), "wb") as out:
            shutil.copyfileobj(fp, out)

    def upload_file(self, key, local_path):
        shutil.copy(local_path, self._path(key))

    def download_to(self, key, local_path):
        Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(self._path(key), local_path)

    def exists(self, key):
        return self._path(key).exists()

    def stat(self, key):
        # Returns size + mtime so callers can build cache keys that
        # invalidate automatically when the underlying file is rewritten.
        st = self._path(key).stat()
        return {"size": int(st.st_size), "mtime": float(st.st_mtime)}

    def local_path(self, key):
        # Lets readers (pyarrow.parquet, ffmpeg, …) consume the file in
        # place instead of via an in-memory bytes buffer, which doubles
        # peak RSS during loads of hundred-MB parquets.
        return str(self._path(key))

    def public_url(self, key):
        # Served by API's /files endpoint in dev
        return f"/files/{key}"

    def signed_url(self, key, expires_minutes=60):
        return self.public_url(key)

    def open_read(self, key):
        return open(self._path(key), "rb")

    def delete_prefix(self, prefix):
        p = self.root / prefix
        if p.exists():
            shutil.rmtree(p)


class GCSStorage(Storage):
    def __init__(self, bucket_name: str):
        from google.cloud import storage as gcs
        self.client = gcs.Client()
        self.bucket = self.client.bucket(bucket_name)
        self.bucket_name = bucket_name

    def upload_stream(self, key, fp):
        blob = self.bucket.blob(key)
        blob.upload_from_file(fp, rewind=True)

    def upload_file(self, key, local_path):
        self.bucket.blob(key).upload_from_filename(local_path)

    def download_to(self, key, local_path):
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        self.bucket.blob(key).download_to_filename(local_path)

    def exists(self, key):
        return self.bucket.blob(key).exists(self.client)

    def stat(self, key):
        blob = self.bucket.blob(key)
        blob.reload()  # populate size/updated from GCS metadata
        return {
            "size": int(blob.size or 0),
            "mtime": (blob.updated.timestamp() if blob.updated else 0.0),
        }

    def local_path(self, key):
        # No on-disk path in GCS — callers fall back to open_read().
        return None

    def public_url(self, key):
        return f"https://storage.googleapis.com/{self.bucket_name}/{key}"

    def signed_url(self, key, expires_minutes=60):
        return self.bucket.blob(key).generate_signed_url(
            expiration=timedelta(minutes=expires_minutes),
            method="GET",
            version="v4",
        )

    def open_read(self, key):
        import io
        data = self.bucket.blob(key).download_as_bytes()
        return io.BytesIO(data)

    def delete_prefix(self, prefix):
        for blob in self.client.list_blobs(self.bucket_name, prefix=prefix):
            blob.delete()


def get_storage() -> Storage:
    if settings.storage_backend == "gcs":
        if not settings.gcs_bucket:
            raise RuntimeError("STORAGE_BACKEND=gcs requires GCS_BUCKET")
        return GCSStorage(settings.gcs_bucket)
    return LocalStorage(settings.local_storage_root)


# Key helpers — keep producers/consumers in sync.
def key_video(project_id: str, video_id: str, filename: str) -> str:
    # Preserve the extension so codecs/decoders are happy
    ext = os.path.splitext(filename)[1].lower() or ".mp4"
    return f"projects/{project_id}/videos/{video_id}/source{ext}"

def key_tracks(project_id: str, video_id: str) -> str:
    return f"projects/{project_id}/videos/{video_id}/tracks.parquet"

def key_tracks_segment(project_id: str, video_id: str, segment_idx: int) -> str:
    return f"projects/{project_id}/videos/{video_id}/tracks_segment_{segment_idx:04d}.parquet"

def key_scene_frame(project_id: str, video_id: str, n: int) -> str:
    return f"projects/{project_id}/videos/{video_id}/frames/{n}.jpg"

def key_frame(project_id: str, video_id: str) -> str:
    return f"projects/{project_id}/videos/{video_id}/frame.jpg"

def key_trajectories(project_id: str, video_id: str) -> str:
    return f"projects/{project_id}/videos/{video_id}/trajectories.png"

def key_heatmap(project_id: str, video_id: str) -> str:
    return f"projects/{project_id}/videos/{video_id}/heatmap.png"

def key_export(project_id: str, export_id: str) -> str:
    return f"projects/{project_id}/exports/{export_id}.xlsx"
