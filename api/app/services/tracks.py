"""Load and combine track Parquet files.

The per-video parquet is cached in a process-local LRU sized by total
bytes. Before the cache, every POST /videos/{id}/counts re-read the
entire parquet off disk and parsed it into a fresh DataFrame; on long
videos that's hundreds of MB per request, which OOM-killed the API
worker when a user drew several lines in quick succession.
"""
from __future__ import annotations
import io
import os
import threading
from collections import OrderedDict
from typing import List, Tuple

import pandas as pd

from .storage import get_storage, key_tracks


_EMPTY_COLS = [
    "frame_idx", "t_seconds", "track_id", "class_id", "conf",
    "cx", "cy", "w", "h",
]

_CACHE_MAX_BYTES = int(os.environ.get(
    "TRACKS_CACHE_MAX_BYTES", str(1 * 1024 * 1024 * 1024)
))  # 1 GiB default

_cache: "OrderedDict[Tuple, Tuple[pd.DataFrame, int]]" = OrderedDict()
_cache_bytes = 0
_cache_lock = threading.Lock()


def _cache_get(ck: tuple) -> pd.DataFrame | None:
    with _cache_lock:
        hit = _cache.get(ck)
        if hit is None:
            return None
        _cache.move_to_end(ck)
        return hit[0]


def _cache_put(ck: tuple, df: pd.DataFrame) -> None:
    global _cache_bytes
    approx = int(df.memory_usage(deep=True).sum())
    with _cache_lock:
        # Drop any stale entry for the same (project, video) so the cache
        # never holds two generations of the same parquet at once.
        for stale in [k for k in list(_cache) if k[0:2] == ck[0:2] and k != ck]:
            _, sz = _cache.pop(stale)
            _cache_bytes -= sz
        _cache[ck] = (df, approx)
        _cache_bytes += approx
        # Evict oldest entries until we fit; keep at least the one we
        # just inserted (otherwise a single oversized parquet causes a
        # cache-thrash that defeats the purpose).
        while _cache_bytes > _CACHE_MAX_BYTES and len(_cache) > 1:
            _, (_, sz) = _cache.popitem(last=False)
            _cache_bytes -= sz


def load_tracks_for_video(project_id: str, video_id: str) -> pd.DataFrame:
    storage = get_storage()
    key = key_tracks(project_id, video_id)
    if not storage.exists(key):
        return pd.DataFrame(columns=_EMPTY_COLS)
    # Build a cache key that includes file identity (mtime + size) so a
    # re-analysis transparently invalidates the cached DataFrame.
    stat = storage.stat(key)
    ck = (project_id, video_id, stat["mtime"], stat["size"])
    cached = _cache_get(ck)
    if cached is not None:
        return cached
    with storage.open_read(key) as fp:
        df = pd.read_parquet(io.BytesIO(fp.read()))
    _cache_put(ck, df)
    return df


def load_tracks_for_videos(project_id: str, video_ids: List[str]) -> pd.DataFrame:
    """
    Concat across videos. We namespace track_ids by video so the same numeric id
    from two videos doesn't collide when counting unique crossings.
    """
    frames = []
    for vid in video_ids:
        df = load_tracks_for_video(project_id, vid)
        if df.empty:
            continue
        df = df.copy()
        df["video_id"] = vid
        # Compose a unique track id "{vid}:{track_id}". Hashing keeps it int-like.
        df["track_id"] = (vid + ":" + df["track_id"].astype(str)).map(hash)
        frames.append(df)
    if not frames:
        return pd.DataFrame(columns=[*_EMPTY_COLS, "video_id"])
    return pd.concat(frames, ignore_index=True)
