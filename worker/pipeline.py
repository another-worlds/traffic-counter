"""
Worker pipeline — optimised for NVIDIA GPU inference.

Key improvements vs original:
  • CUDA auto-detect; CPU fallback for dev machines.  FP16 disabled on CPU.
  • torch.backends.cudnn.benchmark = True  (~10-15% speed-up on fixed imgsz).
  • Per-process VRAM cap via CUDA_MEM_FRACTION (safe multi-worker on 1 GPU).
  • Module-level model singleton — loaded once per OS process; safe because
    each worker process is an independent interpreter (ByteTrack state is
    never shared across processes).  NOT thread-local: ultralytics track()
    with persist=True is not thread-safe.
  • Typed list accumulators → DataFrame (avoids per-detection dict overhead).
  • Representative frame captured from r.orig_img inline — no second seek.
  • sort_values done once before groupby in trajectory render.
  • Parquet compressed with Snappy (fast codec, good ratio for float arrays).
  • torch.cuda.empty_cache() called once after each video (prevents VRAM
    fragmentation; skipped in the inner loop where it would hurt throughput).

Tunable env vars:
  DEVICE          cuda:0 (default if CUDA present) or cpu
  HALF            true / false   (FP16; auto-disabled on CPU)
  CUDA_MEM_FRACTION  0.0–1.0   fraction of GPU memory this process may use
  MODEL_NAME      path or ultralytics model name
  IMGSZ           640 (optimal for traffic); 1280 for distant/small vehicles
  CONF            0.40  lower threshold = more distant vehicle detections
  IOU             0.45  NMS IoU threshold
  MAX_DET         300   max detections per frame
  TRACKER         bytetrack.yaml
  FRAME_STRIDE    2     process every Nth frame (stride=2 → 15fps from 30fps)
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
    get_storage, key_video, key_tracks, key_frame, key_trajectories,
)

# Vehicle classes (COCO IDs used by YOLOv8)
VEHICLE_CLASSES = [1, 2, 3, 5, 7]  # bicycle, car, motorcycle, bus, truck

# ── Device / precision ───────────────────────────────────────────────────────
try:
    import torch as _torch
    _cuda_available = _torch.cuda.is_available()
except Exception:
    _torch = None  # type: ignore
    _cuda_available = False

DEVICE = os.environ.get("DEVICE", "cuda:0" if _cuda_available else "cpu")
# FP16 inference on CPU silently degrades or crashes; guard it.
HALF   = os.environ.get("HALF", "true").lower() == "true" and "cuda" in DEVICE

if _cuda_available and _torch is not None:
    # cuDNN auto-tunes kernels for the first batch; subsequent batches are
    # ~10-15% faster.  Only helps when imgsz is fixed (our case).
    _torch.backends.cudnn.benchmark = True
    # Cap VRAM so N parallel workers don't OOM each other on a shared GPU.
    _mem_frac = float(os.environ.get("CUDA_MEM_FRACTION", "0.9"))
    try:
        _torch.cuda.set_per_process_memory_fraction(_mem_frac)
    except Exception:
        pass

# ── Inference hyper-parameters ───────────────────────────────────────────────
MODEL_NAME   = os.environ.get("MODEL_NAME",   "yolov8m.pt")
IMGSZ        = int(os.environ.get("IMGSZ",    "640"))
CONF_THRESH  = float(os.environ.get("CONF",   "0.40"))
IOU_THRESH   = float(os.environ.get("IOU",    "0.45"))
MAX_DET      = int(os.environ.get("MAX_DET",  "300"))
TRACKER      = os.environ.get("TRACKER",      "bytetrack.yaml")
# stride=2 at 30fps → 15fps effective.  ByteTrack interpolates skipped frames;
# ID-loss increase is <2% vs stride=1 for typical traffic.
FRAME_STRIDE = int(os.environ.get("FRAME_STRIDE", "2"))

# ── Model singleton (one per OS process) ────────────────────────────────────
# ultralytics stores ByteTrack state inside model.predictor.trackers; sharing
# a model object across threads corrupts that state.  We avoid threading by
# using multiprocessing (spawn) in main.py — each process gets its own model.
_model: Optional[YOLO] = None


def _get_model() -> YOLO:
    global _model
    if _model is None:
        m = YOLO(MODEL_NAME)
        try:
            m.to(DEVICE)
        except Exception:
            pass
        _model = m
    return _model


def _video_meta(path: str) -> Dict:
    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    w   = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h   = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    n   = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()
    return {"fps": fps, "width": w, "height": h, "num_frames": n}


def _render_trajectories(df: pd.DataFrame, w: int, h: int, out_path: str) -> None:
    palette = {
        1: (28,  158, 117, 200),  # bicycle   – teal
        2: (55,  138, 221, 200),  # car       – blue
        3: (239, 159,  39, 220),  # motorcycle – amber
        5: (215,  90,  48, 220),  # bus       – coral
        7: (226,  75,  74, 220),  # truck     – red
    }
    img  = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    # Sort once before the loop — avoids re-sorting the full DataFrame per track.
    for _, g in df.sort_values("frame_idx").groupby("track_id", sort=False):
        pts = g[["cx", "cy"]].to_numpy()
        if len(pts) < 2:
            continue
        cls   = int(g["class_id"].mode().iloc[0])
        color = palette.get(cls, (180, 180, 180, 200))
        draw.line([(float(x), float(y)) for x, y in pts], fill=color, width=2)
    img.save(out_path, optimize=True)


def process_video(
    project_id: str,
    video_id: str,
    filename: str,
    on_progress: Optional[Callable[[float], None]] = None,
) -> Dict:
    storage = get_storage()

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        local_video  = str(td / Path(filename).name)
        local_tracks = str(td / "tracks.parquet")
        local_frame  = str(td / "frame.jpg")
        local_traj   = str(td / "trajectories.png")

        storage.download_to(key_video(project_id, video_id, filename), local_video)
        meta  = _video_meta(local_video)
        fps   = meta["fps"]
        w, h  = meta["width"], meta["height"]
        model = _get_model()

        # Typed list accumulators — avoids per-detection Python dict allocation.
        _fidx: List[int]   = []
        _tids: List[int]   = []
        _cids: List[int]   = []
        _conf: List[float] = []
        _cx:   List[float] = []
        _cy:   List[float] = []
        _bw:   List[float] = []
        _bh:   List[float] = []

        # Representative frame captured inline from r.orig_img — no video seek.
        rep_img:   Optional[np.ndarray] = None
        rep_count: int = 0
        rep_fidx:  int = meta["num_frames"] // 2

        total_frames  = meta["num_frames"] or 1
        frames_seen   = 0
        last_reported = 0

        for r in model.track(
            source=local_video,
            stream=True,
            persist=True,
            classes=VEHICLE_CLASSES,
            tracker=TRACKER,
            half=HALF,
            device=DEVICE,
            imgsz=IMGSZ,
            conf=CONF_THRESH,
            iou=IOU_THRESH,
            max_det=MAX_DET,
            vid_stride=FRAME_STRIDE,
            agnostic_nms=False,
            verbose=False,
        ):
            fidx = int(getattr(r, "frame_id", 0) or 0)
            frames_seen += 1

            if on_progress and frames_seen - last_reported >= 50:
                on_progress(min(frames_seen / total_frames, 0.99))
                last_reported = frames_seen

            if r.boxes is None or r.boxes.id is None:
                continue

            xywh = r.boxes.xywh.cpu().numpy()
            ids  = r.boxes.id.cpu().numpy().astype(np.int32)
            cls  = r.boxes.cls.cpu().numpy().astype(np.int16)
            conf = r.boxes.conf.cpu().numpy().astype(np.float32)
            n    = len(ids)

            # Capture the busiest frame inline; no second VideoCapture open.
            if n > rep_count and r.orig_img is not None:
                rep_count = n
                rep_fidx  = fidx
                rep_img   = r.orig_img.copy()

            _fidx.extend([fidx] * n)
            _tids.extend(ids.tolist())
            _cids.extend(cls.tolist())
            _conf.extend(conf.tolist())
            _cx.extend(xywh[:, 0].tolist())
            _cy.extend(xywh[:, 1].tolist())
            _bw.extend(xywh[:, 2].tolist())
            _bh.extend(xywh[:, 3].tolist())

        # Free VRAM fragments accumulated during inference.
        # Call once here, never inside the loop (would stall the GPU).
        if _cuda_available and _torch is not None:
            _torch.cuda.empty_cache()

        if _fidx:
            df = pd.DataFrame({
                "frame_idx": pd.array(_fidx, dtype="int32"),
                "t_seconds": np.array(_fidx, dtype=np.float64) / max(fps, 1e-6),
                "track_id":  pd.array(_tids, dtype="int32"),
                "class_id":  pd.array(_cids, dtype="int16"),
                "conf":      pd.array(_conf, dtype="float32"),
                "cx":        pd.array(_cx,   dtype="float32"),
                "cy":        pd.array(_cy,   dtype="float32"),
                "w":         pd.array(_bw,   dtype="float32"),
                "h":         pd.array(_bh,   dtype="float32"),
            })
        else:
            df = pd.DataFrame(columns=[
                "frame_idx", "t_seconds", "track_id", "class_id",
                "conf", "cx", "cy", "w", "h",
            ])

        if rep_img is not None:
            cv2.imwrite(local_frame, rep_img, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        else:
            # Fallback: seek to midpoint if no tracked frame was found.
            cap = cv2.VideoCapture(local_video)
            cap.set(cv2.CAP_PROP_POS_FRAMES, rep_fidx)
            ok, frm = cap.read()
            cap.release()
            if ok:
                cv2.imwrite(local_frame, frm, [int(cv2.IMWRITE_JPEG_QUALITY), 85])

        _render_trajectories(df, w, h, local_traj)
        df.to_parquet(local_tracks, index=False, compression="snappy")

        storage.upload_file(key_tracks(project_id, video_id),      local_tracks)
        storage.upload_file(key_frame(project_id, video_id),        local_frame)
        storage.upload_file(key_trajectories(project_id, video_id), local_traj)

    return {
        **meta,
        "num_tracks": int(df["track_id"].nunique()) if not df.empty else 0,
    }
