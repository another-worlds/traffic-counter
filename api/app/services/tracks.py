"""Load and combine track Parquet files.

The per-video parquet is cached in a process-local LRU sized by total
bytes. Before the cache, every POST /videos/{id}/counts re-read the
entire parquet off disk and parsed it into a fresh DataFrame; on long
videos that's hundreds of MB per request, which OOM-killed the API
worker when a user drew several lines in quick succession.

A second slot on the same cache entry holds the per-video
MaterializedTracks (sort + groupby + modal-class precomputed) so the
counting code never re-does that work either.
"""
from __future__ import annotations
import io
import os
import threading
from collections import OrderedDict
from typing import List, Optional, Tuple

import pandas as pd

from .counting import (
    MaterializedTracks,
    materialize_tracks,
    materialized_nbytes,
)
from .storage import get_storage, key_tracks


_EMPTY_COLS = [
    "frame_idx", "t_seconds", "track_id", "class_id", "conf",
    "cx", "cy", "w", "h",
]

_CACHE_MAX_BYTES = int(os.environ.get(
    "TRACKS_CACHE_MAX_BYTES", str(1 * 1024 * 1024 * 1024)
))  # 1 GiB default


class _Entry:
    """One cache slot. Carries the parquet DataFrame plus a lazily-built
    MaterializedTracks; the byte size counted against the cache budget
    grows as the entry's materialised half is filled in."""
    __slots__ = ("df", "df_bytes", "mt", "mt_bytes")

    def __init__(self, df: pd.DataFrame, df_bytes: int) -> None:
        self.df = df
        self.df_bytes = df_bytes
        self.mt: Optional[MaterializedTracks] = None
        self.mt_bytes = 0

    @property
    def total_bytes(self) -> int:
        return self.df_bytes + self.mt_bytes


_cache: "OrderedDict[Tuple, _Entry]" = OrderedDict()
_cache_bytes = 0
_cache_lock = threading.Lock()


def _cache_get(ck: tuple) -> Optional[_Entry]:
    with _cache_lock:
        hit = _cache.get(ck)
        if hit is None:
            return None
        _cache.move_to_end(ck)
        return hit


def _evict_locked() -> None:
    """Caller already holds _cache_lock."""
    global _cache_bytes
    while _cache_bytes > _CACHE_MAX_BYTES and len(_cache) > 1:
        _, ev = _cache.popitem(last=False)
        _cache_bytes -= ev.total_bytes


def _cache_put_df(ck: tuple, df: pd.DataFrame) -> _Entry:
    global _cache_bytes
    approx = int(df.memory_usage(deep=True).sum())
    entry = _Entry(df, approx)
    with _cache_lock:
        # Drop any stale entry for the same (project, video) so the cache
        # never holds two generations of the same parquet at once.
        for stale in [k for k in list(_cache) if k[0:2] == ck[0:2] and k != ck]:
            ev = _cache.pop(stale)
            _cache_bytes -= ev.total_bytes
        _cache[ck] = entry
        _cache_bytes += entry.total_bytes
        _evict_locked()
    return entry


def _attach_materialized_locked(entry: _Entry, mt: MaterializedTracks) -> None:
    """Caller already holds _cache_lock."""
    global _cache_bytes
    entry.mt = mt
    entry.mt_bytes = materialized_nbytes(mt)
    _cache_bytes += entry.mt_bytes
    _evict_locked()


def _resolve_cache_key(project_id: str, video_id: str) -> Optional[tuple]:
    storage = get_storage()
    key = key_tracks(project_id, video_id)
    if not storage.exists(key):
        return None
    stat = storage.stat(key)
    return (project_id, video_id, stat["mtime"], stat["size"])


def load_tracks_for_video(project_id: str, video_id: str) -> pd.DataFrame:
    ck = _resolve_cache_key(project_id, video_id)
    if ck is None:
        return pd.DataFrame(columns=_EMPTY_COLS)
    entry = _cache_get(ck)
    if entry is not None:
        return entry.df
    storage = get_storage()
    with storage.open_read(key_tracks(project_id, video_id)) as fp:
        df = pd.read_parquet(io.BytesIO(fp.read()))
    _cache_put_df(ck, df)
    return df


def load_materialized_tracks(project_id: str, video_id: str) -> MaterializedTracks:
    """Return the per-video MaterializedTracks, building (and caching) it on
    first use. Cache identity matches the parquet cache so a re-analysis
    transparently invalidates both halves at once."""
    ck = _resolve_cache_key(project_id, video_id)
    if ck is None:
        # No tracks on disk yet; materialise the empty frame so callers don't
        # need to special-case the missing-file path.
        return materialize_tracks(pd.DataFrame(columns=_EMPTY_COLS))
    entry = _cache_get(ck)
    if entry is None:
        storage = get_storage()
        with storage.open_read(key_tracks(project_id, video_id)) as fp:
            df = pd.read_parquet(io.BytesIO(fp.read()))
        entry = _cache_put_df(ck, df)
    if entry.mt is not None:
        return entry.mt
    # Build outside the lock — materialise can take a second or two on huge
    # videos and we don't want to block concurrent readers of *other* videos.
    mt = materialize_tracks(entry.df)
    with _cache_lock:
        # Re-check after acquiring: another caller may have raced us.
        existing = _cache.get(ck)
        if existing is not None and existing.mt is None:
            _attach_materialized_locked(existing, mt)
        elif existing is not None and existing.mt is not None:
            mt = existing.mt
    return mt


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
