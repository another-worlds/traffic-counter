"""
Worker pipeline.

Steps per video segment:
  1. Open the source video and seek to the segment's start frame.
  2. Run Ultralytics YOLO + ByteTrack on frames [start_frame, end_frame).
  3. For each frame, append every tracked vehicle's centre to a list.
  4. Write tracks_segment_{idx:04d}.parquet to storage.

Finalisation (after all segments are done):
  5. Detect scene/angle changes across the full video.
  6. Extract one representative JPEG per scene and upload.
  7. Load all segment parquets, render a single trajectory overlay PNG
     for the whole video, upload it.

Segment parquet schema:
  frame_idx int32, t_seconds float32, track_id int32,
  class_id int8, conf float32, cx float32, cy float32, w float32, h float32

Track IDs are local to each segment (ByteTrack resets between segments).
Consumers that merge segments must offset them by segment_idx * TRACK_ID_SEGMENT_OFFSET
to avoid collisions.
"""
from __future__ import annotations
import os
import queue
import tempfile
import threading
from pathlib import Path
from typing import Callable, Dict, Generator, List, Optional, Tuple

import cv2
import numpy as np
import pandas as pd
import torch
from PIL import Image, ImageDraw

from ultralytics import YOLO

# Video frames are a fixed resolution, so the model sees a constant input
# shape every forward pass. cuDNN autotuning picks the fastest conv kernels
# once and reuses them — a free single-GPU throughput win for our workload.
torch.backends.cudnn.benchmark = True

from storage import (
    get_storage, key_video, key_tracks, key_tracks_segment,
    key_frame, key_scene_frame, key_trajectories,
)

# Vehicle classes (COCO defaults that Ultralytics yolov8 uses).
VEHICLE_CLASSES = [1, 2, 3, 5, 7]  # bicycle, car, motorcycle, bus, truck

# Consumers merge all segment parquets into one DataFrame.  Offset each
# segment's track_ids by this multiplier so IDs from different hours
# never collide when grouping or drawing trajectories.
# CONTRACT: must equal api/app/services/tracks.py:_TRACK_ID_SEGMENT_OFFSET.
TRACK_ID_SEGMENT_OFFSET = 1_000_000

# Canonical on-disk schema for tracks_segment_*.parquet. The producer pins
# these dtypes before writing so the API reader, counting, and export all see
# exactly this contract.
# CONTRACT: must equal api/app/services/tracks.py:_DTYPES.
TRACKS_PARQUET_DTYPES = {
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
# Frames fed to the GPU per forward pass. Higher = better GPU utilisation.
BATCH_SIZE = int(os.environ.get("BATCH_SIZE", "16"))
# How many frame batches to decode ahead of the GPU. A background thread keeps
# the queue full so inference never waits on cv2 decode. Peak RAM is bounded to
# ~(FRAME_PREFETCH_BATCHES + 1) * BATCH_SIZE frames.
FRAME_PREFETCH_BATCHES = int(os.environ.get("FRAME_PREFETCH_BATCHES", "3"))
# How many GPU result batches may queue for the post-process thread. Keeps the
# GPU launching the next batch while the previous one's detections are copied to
# CPU and turned into rows. Bounded small so retained tensors don't grow VRAM.
RESULT_QUEUE_DEPTH = int(os.environ.get("RESULT_QUEUE_DEPTH", "3"))
# Seconds without a decoded frame before the decode thread is considered stalled.
# 120 s is safe: cv2 decodes a 16-frame 2560×1440 HEVC batch in < 2 s even under
# heavy error-concealment. A truly stalled cap.read() (corrupt keyframe, HEVC POC
# error that never resolves) would never recover without this timeout.
DECODE_STALL_TIMEOUT_S = int(os.environ.get("DECODE_STALL_TIMEOUT_S", "120"))
# Cap on scene-keyframes emitted per video.
MAX_SCENE_FRAMES = int(os.environ.get("MAX_SCENE_FRAMES", "30"))
# Default segment length in seconds (1 hour).
SEGMENT_DURATION_S = float(os.environ.get("SEGMENT_DURATION_S", "3600"))


_MODEL: Optional[YOLO] = None
_MODEL_LOCK = threading.Lock()


def _load_model() -> YOLO:
    model = YOLO(MODEL_NAME)
    try:
        model.to(DEVICE)
    except Exception:
        pass
    return model


def _get_model() -> YOLO:
    """Return the process-wide YOLO instance, loading it once.

    The model is loaded a single time per worker process instead of once per
    segment: reloading the weights every hour-segment wasted ~0.5s and forced
    cuDNN to re-autotune on the first batch of each segment. ByteTrack state is
    still reset per segment by the caller (model.predictor = None).
    """
    global _MODEL
    if _MODEL is None:
        with _MODEL_LOCK:
            if _MODEL is None:
                _MODEL = _load_model()
    return _MODEL


def video_meta(path: str) -> Dict:
    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()
    return {"fps": fps, "width": w, "height": h, "num_frames": n}


def plan_video_segments(
    fps: float, num_frames: int, segment_duration_s: float = SEGMENT_DURATION_S
) -> List[Dict]:
    """Return the list of segment descriptors for this video.

    Each descriptor has keys: segment_idx, start_frame, end_frame,
    start_time_s, end_time_s.
    """
    frames_per_seg = max(1, int(round(fps * segment_duration_s)))
    segments = []
    idx = 0
    f = 0
    while f < num_frames:
        end_f = min(f + frames_per_seg, num_frames)
        segments.append({
            "segment_idx": idx,
            "start_frame": f,
            "end_frame": end_f,
            "start_time_s": round(f / fps, 3) if fps > 0 else 0.0,
            "end_time_s": round(end_f / fps, 3) if fps > 0 else 0.0,
        })
        f = end_f
        idx += 1
    return segments


def _frame_generator(
    video_path: str, start_frame: int, end_frame: int, stride: int = 1,
    read_to_eof: bool = False, fps: float = 25.0,
) -> Generator:
    """Yield BGR frames from [start_frame, end_frame) with optional stride.

    When read_to_eof is True the end_frame bound is ignored and frames are
    yielded until the decoder reports EOF. Used for the final segment so a
    too-low CAP_PROP_FRAME_COUNT estimate can't silently drop the video tail.
    """
    cap = cv2.VideoCapture(video_path)
    if start_frame > 0:
        # MSEC seek lands on the nearest keyframe (fast on long-GOP HEVC/H.264).
        # cap.grab() advances to the exact target frame without full decode.
        start_ms = start_frame / fps * 1000.0 if fps > 0 else 0.0
        cap.set(cv2.CAP_PROP_POS_MSEC, start_ms)
        pos = int(cap.get(cv2.CAP_PROP_POS_FRAMES))
        while pos < start_frame:
            if not cap.grab():
                break
            pos += 1
    current = start_frame
    while read_to_eof or current < end_frame:
        ok, frame = cap.read()
        if not ok:
            break
        yield frame
        current += stride
        if stride > 1 and (read_to_eof or current < end_frame):
            cap.set(cv2.CAP_PROP_POS_FRAMES, current)
    cap.release()


_BATCH_SENTINEL = object()


def _iter_frame_batches(
    video_path: str,
    start_frame: int,
    end_frame: int,
    stride: int,
    batch_size: int,
    max_prefetch: int,
    read_to_eof: bool = False,
    fps: float = 25.0,
) -> Generator:
    """Yield BATCH_SIZE-sized frame lists, decoded on a background thread.

    A daemon producer thread runs cv2 decode and fills a bounded queue while the
    caller (the GPU inference loop) drains it, so the GPU never waits on frame
    decode. Memory is bounded to ~(max_prefetch + 1) * batch_size frames.
    Producer-side exceptions are re-raised in the consumer; the producer is
    stopped and joined when the consumer exits early (break or exception).
    """
    q: "queue.Queue" = queue.Queue(maxsize=max(1, max_prefetch))
    stop = threading.Event()

    def _produce() -> None:
        try:
            batch: list = []
            for frame in _frame_generator(video_path, start_frame, end_frame, stride, read_to_eof, fps):
                if stop.is_set():
                    return
                batch.append(frame)
                if len(batch) >= batch_size:
                    # Timeout loop so a stopped consumer can't deadlock the put.
                    while not stop.is_set():
                        try:
                            q.put(batch, timeout=0.5)
                            break
                        except queue.Full:
                            continue
                    batch = []
            if batch and not stop.is_set():
                while not stop.is_set():
                    try:
                        q.put(batch, timeout=0.5)
                        break
                    except queue.Full:
                        continue
        except Exception as exc:  # surface decode errors to the consumer
            try:
                q.put(exc, timeout=0.5)
            except queue.Full:
                pass
        finally:
            # Retry until the GPU consumer drains enough space. A single-attempt
            # put with queue.Full silently drops the sentinel and leaves the GPU
            # loop blocked on q.get() forever (Bug A fix).
            while not stop.is_set():
                try:
                    q.put(_BATCH_SENTINEL, timeout=0.5)
                    break
                except queue.Full:
                    continue

    thread = threading.Thread(target=_produce, name="frame-prefetch", daemon=True)
    thread.start()
    try:
        while True:
            try:
                item = q.get(timeout=DECODE_STALL_TIMEOUT_S)
            except queue.Empty:
                stop.set()
                raise RuntimeError(
                    f"Frame decode stalled: no batch produced in "
                    f"{DECODE_STALL_TIMEOUT_S}s — cv2 likely hung on a "
                    f"corrupted frame"
                )
            if item is _BATCH_SENTINEL:
                break
            if isinstance(item, Exception):
                raise item
            yield item
    finally:
        stop.set()
        # Drain so a producer blocked on put() can observe the stop flag.
        try:
            while True:
                q.get_nowait()
        except queue.Empty:
            pass
        thread.join(timeout=5)


def process_video_segment(
    project_id: str,
    video_id: str,
    segment_idx: int,
    video_path: str,
    start_frame: int,
    end_frame: int,
    fps: float,
    on_progress: Optional[Callable[[float], None]] = None,
    read_to_eof: bool = False,
) -> tuple[int, int]:
    """Run YOLO+ByteTrack on one segment of the video.

    Writes ``tracks_segment_{segment_idx:04d}.parquet`` to storage and returns
    ``(num_tracks, actual_end_frame)``: the number of unique track IDs found in
    this segment, and the exclusive absolute frame index one past the last frame
    actually decoded. Track IDs are local to the segment (ByteTrack state resets
    each call).

    When read_to_eof is True the end_frame bound is treated only as a progress
    estimate and decoding continues to the true EOF, so the final segment cannot
    silently drop frames past a too-low CAP_PROP_FRAME_COUNT estimate. The
    returned actual_end_frame lets the caller persist the real range.
    """
    storage = get_storage()
    model = _get_model()

    # Reset any cached predictor so ByteTrack starts fresh for this segment.
    if getattr(model, "predictor", None) is not None:
        model.predictor = None

    rows: List[dict] = []
    total_seg_frames = max(1, (end_frame - start_frame + FRAME_STRIDE - 1) // FRAME_STRIDE)
    # Mutable progress counters, owned by the post-process consumer thread.
    prog = {"done": 0, "reported": 0}

    # Three-stage pipeline so the GPU never idles:
    #   1. a decode thread (_iter_frame_batches) keeps frame batches ready;
    #   2. THIS thread runs model.track(batch) and immediately hands the GPU
    #      results to a queue, then launches the next batch;
    #   3. a post-process thread copies detections to CPU and builds rows.
    # Ultralytics rejects a generator source ("Unsupported image type"), so we
    # pass fixed-size frame lists; persist=True carries ByteTrack state across
    # batches (reset only between segments via model.predictor=None above).
    batches = _iter_frame_batches(
        video_path, start_frame, end_frame, FRAME_STRIDE,
        BATCH_SIZE, FRAME_PREFETCH_BATCHES, read_to_eof, fps,
    )

    results_q: "queue.Queue" = queue.Queue(maxsize=max(1, RESULT_QUEUE_DEPTH))
    stop = threading.Event()
    consumer_err: dict = {}

    def _consume() -> None:
        try:
            while True:
                item = results_q.get()
                if item is _BATCH_SENTINEL:
                    return
                start_abs, results = item
                frame_idx = start_abs
                for r in results:
                    cur = frame_idx
                    frame_idx += FRAME_STRIDE
                    prog["done"] += 1
                    if on_progress and prog["done"] - prog["reported"] >= 50:
                        on_progress(min(prog["done"] / total_seg_frames, 0.99))
                        prog["reported"] = prog["done"]

                    if r.boxes is None or r.boxes.id is None:
                        continue
                    xywh = r.boxes.xywh.cpu().numpy()
                    ids = r.boxes.id.cpu().numpy().astype(np.int32)
                    cls = r.boxes.cls.cpu().numpy().astype(np.int16)
                    conf = r.boxes.conf.cpu().numpy().astype(np.float32)
                    for k in range(len(ids)):
                        rows.append({
                            "frame_idx": cur,
                            "t_seconds": cur / fps if fps > 0 else 0.0,
                            "track_id": int(ids[k]),
                            "class_id": int(cls[k]),
                            "conf": float(conf[k]),
                            "cx": float(xywh[k][0]),
                            "cy": float(xywh[k][1]),
                            "w": float(xywh[k][2]),
                            "h": float(xywh[k][3]),
                        })
        except Exception as exc:  # surface to the main thread; unblock producer
            consumer_err["exc"] = exc
            stop.set()
            try:
                while True:
                    results_q.get_nowait()
            except queue.Empty:
                pass

    consumer = threading.Thread(target=_consume, name="result-postprocess", daemon=True)
    consumer.start()

    # Frame index within the ORIGINAL video (absolute).
    abs_frame = start_frame
    try:
        for batch_frames in batches:
            if stop.is_set():
                break
            results = model.track(
                source=batch_frames,
                persist=True,
                classes=VEHICLE_CLASSES,
                tracker=TRACKER,
                half=HALF,
                device=DEVICE,
                verbose=False,
            )
            # Hand the GPU results off; main thread loops on to the next batch.
            while not stop.is_set():
                try:
                    results_q.put((abs_frame, results), timeout=0.5)
                    break
                except queue.Full:
                    continue
            abs_frame += FRAME_STRIDE * len(results)
    finally:
        # Retry until the consumer drains a slot. A single-attempt put with
        # queue.Full silently drops the sentinel, leaving the consumer blocked
        # on results_q.get() for the full consumer.join(timeout=60) window
        # at the end of every segment (Bug B fix).
        while not stop.is_set():
            try:
                results_q.put(_BATCH_SENTINEL, timeout=0.5)
                break
            except queue.Full:
                continue
        consumer.join(timeout=60)

    if consumer_err.get("exc") is not None:
        raise consumer_err["exc"]

    if rows:
        df = pd.DataFrame(rows).astype(TRACKS_PARQUET_DTYPES)
    else:
        df = pd.DataFrame({c: pd.Series(dtype=t) for c, t in TRACKS_PARQUET_DTYPES.items()})
    num_tracks = int(df["track_id"].nunique()) if not df.empty else 0

    with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as tf:
        tmp_path = tf.name
    try:
        df.to_parquet(tmp_path, index=False)
        storage.upload_file(key_tracks_segment(project_id, video_id, segment_idx), tmp_path)
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    if on_progress:
        on_progress(1.0)
    # abs_frame has advanced by FRAME_STRIDE per decoded frame, so it is the
    # exclusive absolute index one past the last frame processed — the true end.
    return num_tracks, abs_frame


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
    """Detect scene/angle changes and return one keyframe per scene."""
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
        if MAX_SCENE_FRAMES >= 1 and len(result) > MAX_SCENE_FRAMES:
            if MAX_SCENE_FRAMES == 1:
                sampled = [result[len(result) // 2]]
            else:
                step = (len(result) - 1) / (MAX_SCENE_FRAMES - 1)
                sampled = [result[round(i * step)] for i in range(MAX_SCENE_FRAMES)]
            for new_i, sf in enumerate(sampled):
                sf["index"] = new_i
            result = sampled
        return result
    except Exception:
        return fallback


def _render_trajectories(df: pd.DataFrame, w: int, h: int, out_path: str) -> None:
    palette = {
        1: (28, 158, 117, 200),
        2: (55, 138, 221, 200),
        3: (239, 159, 39, 220),
        5: (215, 90, 48, 220),
        7: (226, 75, 74, 220),
    }
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    for tid, g in df.sort_values("frame_idx").groupby("track_id", sort=False):
        pts = g[["cx", "cy"]].to_numpy()
        if pts.shape[0] < 2:
            continue
        cls = int(g["class_id"].mode().iloc[0])
        color = palette.get(cls, (180, 180, 180, 200))
        flat = [(float(x), float(y)) for x, y in pts]
        draw.line(flat, fill=color, width=2)

    img.save(out_path, optimize=True)


def finalize_video_post_processing(
    project_id: str,
    video_id: str,
    video_path: str,
    meta: Dict,
    total_segments: int,
    on_progress: Optional[Callable[[float], None]] = None,
) -> Tuple[List[dict], int]:
    """Run scene detection, upload keyframes, render and upload the
    whole-video trajectory PNG.

    Returns (scene_frames, total_num_tracks).  Called once after all
    segments have been processed.
    """
    storage = get_storage()
    fps = meta["fps"]
    w, h = meta["width"], meta["height"]
    num_frames = meta["num_frames"] or 1

    # Load all segment parquets and concat for trajectory rendering.
    seg_dfs: List[pd.DataFrame] = []
    total_tracks = 0
    for seg_idx in range(total_segments):
        seg_key = key_tracks_segment(project_id, video_id, seg_idx)
        if not storage.exists(seg_key):
            continue
        local = getattr(storage, "local_path", lambda _k: None)(seg_key)
        if local:
            seg_df = pd.read_parquet(local)
        else:
            import io
            with storage.open_read(seg_key) as fp:
                seg_df = pd.read_parquet(io.BytesIO(fp.read()))
        if not seg_df.empty:
            # Offset track IDs so they don't collide across segments.
            seg_df = seg_df.copy()
            seg_df["track_id"] = seg_df["track_id"] + seg_idx * TRACK_ID_SEGMENT_OFFSET
            total_tracks += int(seg_df["track_id"].nunique())
        seg_dfs.append(seg_df)

    all_tracks = pd.concat(seg_dfs, ignore_index=True) if seg_dfs else pd.DataFrame(
        columns=["frame_idx", "t_seconds", "track_id", "class_id", "conf",
                 "cx", "cy", "w", "h"]
    )

    if on_progress:
        on_progress(0.9)

    scenes = _detect_scenes(video_path, fps, num_frames)

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)

        if on_progress:
            on_progress(0.92)

        for scene in scenes:
            local_sf = str(td / f"frame_{scene['index']}.jpg")
            _grab_frame(video_path, scene["frame_index_in_video"], local_sf)
            storage.upload_file(
                key_scene_frame(project_id, video_id, scene["index"]),
                local_sf,
            )
            if on_progress:
                on_progress(0.93)

        if scenes:
            storage.upload_file(
                key_frame(project_id, video_id),
                str(td / f"frame_{scenes[0]['index']}.jpg"),
            )

        local_traj = str(td / "trajectories.png")
        _render_trajectories(all_tracks, w, h, local_traj)
        storage.upload_file(key_trajectories(project_id, video_id), local_traj)

    if on_progress:
        on_progress(1.0)

    return scenes, total_tracks
