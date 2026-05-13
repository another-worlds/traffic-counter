"""Load and combine track Parquet files."""
from __future__ import annotations
import pandas as pd
import io
from typing import List
from .storage import get_storage, key_tracks


def load_tracks_for_video(project_id: str, video_id: str) -> pd.DataFrame:
    storage = get_storage()
    key = key_tracks(project_id, video_id)
    if not storage.exists(key):
        return pd.DataFrame(
            columns=["frame_idx", "t_seconds", "track_id", "class_id", "conf", "cx", "cy", "w", "h"]
        )
    with storage.open_read(key) as fp:
        return pd.read_parquet(io.BytesIO(fp.read()))


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
        return pd.DataFrame(
            columns=["frame_idx", "t_seconds", "track_id", "class_id", "conf",
                     "cx", "cy", "w", "h", "video_id"]
        )
    return pd.concat(frames, ignore_index=True)
