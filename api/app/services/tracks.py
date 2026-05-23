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
import ctypes
import gc
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
from .storage import get_storage, key_tracks, key_tracks_segment


_EMPTY_COLS = [
    "frame_idx", "t_seconds", "track_id", "class_id", "conf",
    "cx", "cy", "w", "h",
]

# Tight compact dtype set used after every parquet load. Pyarrow → pandas
# can upcast int/float columns depending on version; pinning these keeps
# the cached DataFrame compact regardless of pyarrow's defaults.
_DTYPES = {
    "frame_idx": "int32",
    "t_seconds": "float32",
    "track_id":  "int32",
    "class_id":  "int8",
    "conf":      "float32",
    "cx":        "float32",
    "cy":        "float32",
    "w":         "float32",
    "h":         "float32",
}

# 512 MiB default — sized to fit ~6-8 long videos cached at once inside the
# 4 GiB Docker mem_limit while leaving headroom for transient working
# memory during sorts/groupbys.
_CACHE_MAX_BYTES = int(os.environ.get(
    "TRACKS_CACHE_MAX_BYTES", str(512 * 1024 * 1024)
))


# glibc malloc holds freed pages indefinitely; Docker measures RSS, not the
# live working set, so a transient parquet load can leave the container's
# memory footprint elevated for the rest of its life. Calling malloc_trim
# after evictions and big allocations returns pages to the kernel
# explicitly. Skip silently on non-glibc systems.
try:
    _libc = ctypes.CDLL("libc.so.6", use_errno=True)
    _malloc_trim = _libc.malloc_trim
    _malloc_trim.argtypes = [ctypes.c_size_t]
    _malloc_trim.restype = ctypes.c_int
except (OSError, AttributeError):
    _malloc_trim = None


def _release_pages() -> None:
    gc.collect()
    if _malloc_trim is not None:
        _malloc_trim(0)


def _normalise_dtypes(df: pd.DataFrame) -> pd.DataFrame:
    for col, dt in _DTYPES.items():
        if col in df.columns and str(df[col].dtype) != dt:
            df[col] = df[col].astype(dt, copy=False)
    return df


def _read_parquet(storage, key: str) -> pd.DataFrame:
    """Prefer on-disk path on backends that support it (LocalStorage); fall
    back to a bytes buffer for GCS-style backends. The path-based read lets
    pyarrow mmap the file and skips a hundred-MB transient bytes copy."""
    local = getattr(storage, "local_path", lambda _k: None)(key)
    if local is not None:
        return pd.read_parquet(local)
    with storage.open_read(key) as fp:
        return pd.read_parquet(io.BytesIO(fp.read()))


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


def _evict_locked() -> bool:
    """Caller already holds _cache_lock. Returns True if anything was evicted."""
    global _cache_bytes
    evicted = False
    while _cache_bytes > _CACHE_MAX_BYTES and len(_cache) > 1:
        _, ev = _cache.popitem(last=False)
        _cache_bytes -= ev.total_bytes
        evicted = True
    return evicted


def _cache_put_df(ck: tuple, df: pd.DataFrame) -> _Entry:
    global _cache_bytes
    approx = int(df.memory_usage(deep=True).sum())
    entry = _Entry(df, approx)
    with _cache_lock:
        # Drop any stale entry for the same (project, video) so the cache
        # never holds two generations of the same parquet at once.
        stale_dropped = False
        for stale in [k for k in list(_cache) if k[0:2] == ck[0:2] and k != ck]:
            ev = _cache.pop(stale)
            _cache_bytes -= ev.total_bytes
            stale_dropped = True
        _cache[ck] = entry
        _cache_bytes += entry.total_bytes
        evicted = _evict_locked()
    # Release pages outside the lock — malloc_trim can take a few ms on big
    # heaps and we don't want to block concurrent cache reads.
    if stale_dropped or evicted:
        _release_pages()
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


# Maximum number of hourly segments we will scan for (100 h ≈ 4 days).
_MAX_SEGMENTS = 100

# Offset applied to track_ids from each segment so IDs from different
# hours never collide when the full DataFrame is assembled.
_TRACK_ID_SEGMENT_OFFSET = 1_000_000


def _resolve_segment_cache_key(project_id: str, video_id: str) -> Optional[tuple]:
    """Return a cache key based on the set of completed segment parquets.

    Returns None if no segment parquets exist yet (fall through to legacy key).
    """
    storage = get_storage()
    stats = []
    for seg_idx in range(_MAX_SEGMENTS):
        key = key_tracks_segment(project_id, video_id, seg_idx)
        if not storage.exists(key):
            break
        st = storage.stat(key)
        stats.append((st["mtime"], st["size"]))
    if not stats:
        return None
    return (project_id, video_id, "segments", len(stats), hash(tuple(stats)))


def _load_segments_df(project_id: str, video_id: str) -> Optional[pd.DataFrame]:
    """Load and concatenate all per-segment parquets, offsetting track IDs.

    Returns None if no segment parquets exist.
    """
    storage = get_storage()
    seg_dfs: list[pd.DataFrame] = []
    for seg_idx in range(_MAX_SEGMENTS):
        key = key_tracks_segment(project_id, video_id, seg_idx)
        if not storage.exists(key):
            break
        df = _normalise_dtypes(_read_parquet(storage, key))
        if not df.empty:
            df = df.copy()
            df["track_id"] = df["track_id"] + seg_idx * _TRACK_ID_SEGMENT_OFFSET
        seg_dfs.append(df)
    if not seg_dfs:
        return None
    return pd.concat(seg_dfs, ignore_index=True)


def load_tracks_for_video(project_id: str, video_id: str) -> pd.DataFrame:
    # Prefer per-segment parquets (segmented 8-24h videos).
    seg_ck = _resolve_segment_cache_key(project_id, video_id)
    if seg_ck is not None:
        entry = _cache_get(seg_ck)
        if entry is not None:
            return entry.df
        df = _load_segments_df(project_id, video_id)
        if df is not None:
            _cache_put_df(seg_ck, df)
            return df

    # Fall back to the legacy single-parquet path (short videos, old data).
    ck = _resolve_cache_key(project_id, video_id)
    if ck is None:
        return pd.DataFrame(columns=_EMPTY_COLS)
    entry = _cache_get(ck)
    if entry is not None:
        return entry.df
    storage = get_storage()
    df = _normalise_dtypes(_read_parquet(storage, key_tracks(project_id, video_id)))
    _cache_put_df(ck, df)
    return df


def load_materialized_tracks(project_id: str, video_id: str) -> MaterializedTracks:
    """Return the per-video MaterializedTracks, building (and caching) it on
    first use. Cache identity matches the parquet cache so a re-analysis
    transparently invalidates both halves at once."""
    # Resolve the appropriate cache key (segment-based or legacy).
    seg_ck = _resolve_segment_cache_key(project_id, video_id)
    ck = seg_ck or _resolve_cache_key(project_id, video_id)

    if ck is None:
        return materialize_tracks(pd.DataFrame(columns=_EMPTY_COLS))

    entry = _cache_get(ck)
    if entry is None:
        if seg_ck is not None:
            df = _load_segments_df(project_id, video_id) or pd.DataFrame(columns=_EMPTY_COLS)
        else:
            storage = get_storage()
            df = _normalise_dtypes(_read_parquet(storage, key_tracks(project_id, video_id)))
        entry = _cache_put_df(ck, df)

    if entry.mt is not None:
        return entry.mt

    mt = materialize_tracks(entry.df)
    with _cache_lock:
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
