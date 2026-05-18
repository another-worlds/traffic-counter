"""
Worker pipeline.

Steps:
  1. Pull source video from storage to a local temp file.
  2. Run Ultralytics YOLO + ByteTrack with stream mode (process frame-by-frame).
  3. For each frame, append every tracked vehicle's center to a list.
  4. Pull a representative frame (the one with the most active tracks) for the UI.
  5. Render trajectory overlay PNG (transparent PNG, lines per track).
  6. Write tracks.parquet, frame.jpg, trajectories.png back to storage.

Outputs (Parquet schema):
  frame_idx int32, t_seconds float64, track_id int32,
  class_id int16, conf float32, cx float32, cy float32, w float32, h float32
"""
from __future__ import annotations
import os
import tempfile
from pathlib import Path
from typing import Callable, Dict, List, Optional

import cv2
import numpy as np
import pandas as pd
from PIL import Image, ImageDraw

from ultralytics import YOLO

from storage import (
    get_storage, key_video, key_tracks, key_frame, key_scene_frame, key_trajectories,
)

# Vehicle classes (COCO defaults that Ultralytics yolov8 uses).
VEHICLE_CLASSES = [1, 2, 3, 5, 7]  # bicycle, car, motorcycle, bus, truck

try:
    from scenedetect import open_video as _sd_open_video, SceneManager as _SceneManager
    from scenedetect.detectors import ContentDetector as _ContentDetector
    _SCENEDETECT_AVAILABLE = True
except ImportError:
    _SCENEDETECT_AVAILABLE = False

MODEL_NAME = os.environ.get("MODEL_NAME", "yolov8m.pt")
DEVICE = os.environ.get("DEVICE", "cuda:0")
HALF = os.environ.get("HALF", "true").lower() == "true"
TRACKER = os.environ.get("TRACKER", "bytetrack.yaml")
FRAME_STRIDE = int(os.environ.get("FRAME_STRIDE", "1"))  # process every Nth frame (1=all)


def _load_model() -> YOLO:
    model = YOLO(MODEL_NAME)
    # Warm up to surface device/half issues early
    try:
        model.to(DEVICE)
    except Exception:
        pass
    return model


def _video_meta(path: str) -> Dict:
    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()
    return {"fps": fps, "width": w, "height": h, "num_frames": n}


def _grab_frame(video_path: str, frame_idx: int, out_path: str) -> bool:
    cap = cv2.VideoCapture(video_path)
    cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, frame_idx))
    ok, frame = cap.read()
    cap.release()
    if not ok:
        return False
    cv2.imwrite(out_path, frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
    return True


def _detect_scenes(video_path: str, fps: float, num_frames: int) -> list:
    """Detect scene/angle changes and return one keyframe per scene.

    Falls back to a single mid-video frame if PySceneDetect is unavailable or
    finds no cuts (uniform footage).
    """
    fallback_idx = max(0, num_frames // 2)
    fallback = [{"index": 0, "frame_index_in_video": fallback_idx,
                 "time_s": round(fallback_idx / fps, 2) if fps > 0 else 0.0}]

    if not _SCENEDETECT_AVAILABLE:
        return fallback

    try:
        video = _sd_open_video(video_path)
        sm = _SceneManager()
        sm.add_detector(_ContentDetector(threshold=27.0))
        sm.detect_scenes(video, show_progress=False)
        scenes = sm.get_scene_list()
        if not scenes:
            return fallback
        result = []
        for i, (start, end) in enumerate(scenes):
            mid_frame = (start.get_frames() + end.get_frames()) // 2
            result.append({
                "index": i,
                "frame_index_in_video": int(mid_frame),
                "time_s": round(mid_frame / fps, 2) if fps > 0 else 0.0,
            })
        return result
    except Exception:
        return fallback


def _render_trajectories(df: pd.DataFrame, w: int, h: int, out_path: str) -> None:
    """
    Render a transparent PNG of all tracks as polylines.
    Colors are stable per class.
    """
    palette = {
        1: (28, 158, 117, 200),   # bicycle - teal
        2: (55, 138, 221, 200),   # car - blue
        3: (239, 159, 39, 220),   # motorcycle - amber
        5: (215, 90, 48, 220),    # bus - coral
        7: (226, 75, 74, 220),    # truck - red
    }
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    for tid, g in df.sort_values("frame_idx").groupby("track_id", sort=False):
        pts = g[["cx", "cy"]].to_numpy()
        if pts.shape[0] < 2:
            continue
        cls = int(g["class_id"].mode().iloc[0])
        color = palette.get(cls, (180, 180, 180, 200))
        # Draw as connected lines
        flat = [(float(x), float(y)) for x, y in pts]
        draw.line(flat, fill=color, width=2)

    img.save(out_path, optimize=True)


def process_video(
    project_id: str,
    video_id: str,
    filename: str,
    on_progress: Optional[Callable[[float], None]] = None,
    local_source_path: Optional[str] = None,
) -> Dict:
    storage = get_storage()

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        local_tracks = str(td / "tracks.parquet")
        local_frame = str(td / "frame.jpg")
        local_traj = str(td / "trajectories.png")

        if local_source_path:
            # Video lives on the watched folder mount — read it directly, no copy.
            local_video = local_source_path
        else:
            src_key = key_video(project_id, video_id, filename)
            local_video = str(td / Path(filename).name)
            storage.download_to(src_key, local_video)
        meta = _video_meta(local_video)
        fps = meta["fps"]
        w, h = meta["width"], meta["height"]

        model = _load_model()

        rows: List[dict] = []
        per_frame_counts: Dict[int, int] = {}
        total_frames = meta["num_frames"] or 1
        frames_processed = 0
        last_reported = 0

        # Ultralytics' track() handles ByteTrack and ID assignment for us.
        # stream=True yields a Results object per frame without storing them all.
        results_iter = model.track(
            source=local_video,
            stream=True,
            persist=True,
            classes=VEHICLE_CLASSES,
            tracker=TRACKER,
            half=HALF,
            device=DEVICE,
            vid_stride=FRAME_STRIDE,
            verbose=False,
        )

        for r in results_iter:
            frame_idx = int(getattr(r, "frame_id", 0) or 0)
            frames_processed += 1

            # Report progress every 50 frames (keeps DB writes negligible)
            if on_progress and frames_processed - last_reported >= 50:
                pct = min(frames_processed / total_frames, 0.99)
                on_progress(pct)
                last_reported = frames_processed

            if r.boxes is None or r.boxes.id is None:
                continue
            xywh = r.boxes.xywh.cpu().numpy()
            ids = r.boxes.id.cpu().numpy().astype(np.int32)
            cls = r.boxes.cls.cpu().numpy().astype(np.int16)
            conf = r.boxes.conf.cpu().numpy().astype(np.float32)
            per_frame_counts[frame_idx] = len(ids)

            for k in range(len(ids)):
                rows.append({
                    "frame_idx": frame_idx,
                    "t_seconds": frame_idx / fps if fps > 0 else 0.0,
                    "track_id": int(ids[k]),
                    "class_id": int(cls[k]),
                    "conf": float(conf[k]),
                    "cx": float(xywh[k][0]),
                    "cy": float(xywh[k][1]),
                    "w": float(xywh[k][2]),
                    "h": float(xywh[k][3]),
                })

        df = pd.DataFrame(rows)
        if df.empty:
            df = pd.DataFrame(columns=[
                "frame_idx", "t_seconds", "track_id", "class_id", "conf",
                "cx", "cy", "w", "h",
            ])

        # Detect scene/angle changes; extract one representative frame per scene.
        scenes = _detect_scenes(local_video, fps, meta["num_frames"] or 1)
        for scene in scenes:
            local_scene_frame = str(td / f"frame_{scene['index']}.jpg")
            _grab_frame(local_video, scene["frame_index_in_video"], local_scene_frame)
            storage.upload_file(
                key_scene_frame(project_id, video_id, scene["index"]),
                local_scene_frame,
            )

        # Also write scene 0 to the legacy frame.jpg key for backward compat.
        if scenes:
            storage.upload_file(
                key_frame(project_id, video_id),
                str(td / f"frame_{scenes[0]['index']}.jpg"),
            )

        _render_trajectories(df, w, h, local_traj)
        df.to_parquet(local_tracks, index=False)

        storage.upload_file(key_tracks(project_id, video_id), local_tracks)
        storage.upload_file(key_trajectories(project_id, video_id), local_traj)

    return {
        **meta,
        "num_tracks": int(df["track_id"].nunique()) if not df.empty else 0,
        "scene_frames": scenes,
    }
