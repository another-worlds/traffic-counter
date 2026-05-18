from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from typing import List

from ..db import get_db
from ..models import Project, Video
from ..schemas import VideoOut, AnalyzeResponse
from ..services.storage import (
    get_storage, key_video, key_frame, key_scene_frame, key_trajectories, key_heatmap,
)
from ..services.jobs import get_job_runner

router = APIRouter(tags=["videos"])


@router.get("/projects/{project_id}/videos", response_model=List[VideoOut])
def list_videos(project_id: str, db: Session = Depends(get_db)):
    if not db.get(Project, project_id):
        raise HTTPException(404, "project not found")
    return (
        db.query(Video)
        .filter(Video.project_id == project_id)
        .order_by(Video.created_at.desc())
        .all()
    )


@router.post("/projects/{project_id}/videos", response_model=VideoOut, status_code=201)
def upload_video(
    project_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """
    Direct upload through the API.

    For production, you would prefer a separate "presign upload" endpoint that
    returns a GCS signed URL and have the client PUT directly to it — skipping
    this byte stream through the API. Kept simple here.
    """
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "project not found")

    v = Video(project_id=project_id, filename=file.filename or "video.mp4",
              storage_path="", status="uploaded")
    db.add(v)
    db.flush()  # get v.id

    v.storage_path = key_video(project_id, v.id, v.filename)

    # Measure file size before streaming (SpooledTemporaryFile supports seek/tell)
    file.file.seek(0, 2)
    v.size_bytes = file.file.tell()
    file.file.seek(0)

    get_storage().upload_stream(v.storage_path, file.file)

    db.commit()
    db.refresh(v)
    return v


@router.get("/videos/{video_id}", response_model=VideoOut)
def get_video(video_id: str, db: Session = Depends(get_db)):
    v = db.get(Video, video_id)
    if not v:
        raise HTTPException(404, "video not found")
    return v


@router.post("/videos/{video_id}/analyze", response_model=AnalyzeResponse)
def analyze_video(video_id: str, db: Session = Depends(get_db)):
    v = db.get(Video, video_id)
    if not v:
        raise HTTPException(404, "video not found")
    if v.status in ("queued", "analyzing"):
        return AnalyzeResponse(video_id=video_id, status=v.status)

    v.status = "queued"
    v.error_message = None
    db.commit()

    get_job_runner().enqueue(video_id=video_id, project_id=v.project_id)
    return AnalyzeResponse(video_id=video_id, status="queued")


@router.get("/videos/{video_id}/frame-url")
def get_frame_url(video_id: str, db: Session = Depends(get_db)):
    v = db.get(Video, video_id)
    if not v:
        raise HTTPException(404, "video not found")
    storage = get_storage()
    k = key_frame(v.project_id, v.id)
    if not storage.exists(k):
        raise HTTPException(404, "frame not ready yet — analyze the video first")
    return {"url": storage.signed_url(k, expires_minutes=60)}


@router.get("/videos/{video_id}/frames")
def list_video_frames(video_id: str, db: Session = Depends(get_db)):
    """Return the list of scene-based keyframe URLs for the given video.

    For videos analyzed before scene detection was added, falls back to the
    single legacy frame.jpg so the viewport still works without re-analysis.
    """
    v = db.get(Video, video_id)
    if not v:
        raise HTTPException(404, "video not found")
    storage = get_storage()
    scenes = v.scene_frames if v.scene_frames else []
    if scenes:
        result = []
        for sf in scenes:
            k = key_scene_frame(v.project_id, v.id, sf["index"])
            url = storage.signed_url(k) if storage.exists(k) else None
            result.append({**sf, "url": url})
        return result
    # Backward compat: serve the legacy single frame as scene 0.
    k = key_frame(v.project_id, v.id)
    url = storage.signed_url(k) if storage.exists(k) else None
    return [{"index": 0, "time_s": 0.0, "frame_index_in_video": 0, "url": url}]


@router.get("/videos/{video_id}/trajectories-url")
def get_trajectories_url(video_id: str, db: Session = Depends(get_db)):
    v = db.get(Video, video_id)
    if not v:
        raise HTTPException(404, "video not found")
    storage = get_storage()
    k = key_trajectories(v.project_id, v.id)
    if not storage.exists(k):
        raise HTTPException(404, "trajectories not ready yet")
    return {"url": storage.signed_url(k, expires_minutes=60)}


@router.get("/videos/{video_id}/heatmap-url")
def get_heatmap_url(video_id: str, db: Session = Depends(get_db)):
    v = db.get(Video, video_id)
    if not v:
        raise HTTPException(404, "video not found")
    if v.status != "analyzed":
        raise HTTPException(409, "video must be analyzed before a heatmap can be generated")
    storage = get_storage()
    k = key_heatmap(v.project_id, v.id)
    if not storage.exists(k):
        # Generate on first request and cache in storage.
        import io as _io
        from ..services.tracks import load_tracks_for_video
        from ..services.heatmap import generate_heatmap
        tracks = load_tracks_for_video(v.project_id, v.id)
        png_bytes = generate_heatmap(tracks, v.width or 1920, v.height or 1080)
        storage.upload_stream(k, _io.BytesIO(png_bytes))
    return {"url": storage.signed_url(k, expires_minutes=60)}


@router.get("/videos/{video_id}/track-stats")
def get_track_stats(video_id: str, db: Session = Depends(get_db)):
    """Aggregate statistics derived from track data for a single analyzed video."""
    v = db.get(Video, video_id)
    if not v:
        raise HTTPException(404, "video not found")
    if v.status != "analyzed":
        raise HTTPException(409, "video must be analyzed first")

    import numpy as np
    from ..services.tracks import load_tracks_for_video
    from ..services.suggest import GRID_N

    df = load_tracks_for_video(v.project_id, v.id)

    _NAMES = {1: "bicycle", 2: "car", 3: "motorcycle", 5: "bus", 7: "truck"}
    _empty = {"total_tracks": 0, "by_class": {}, "busy_zone": None,
              "avg_track_frames": 0.0,
              "direction_bins": {"right": 0, "left": 0, "up": 0, "down": 0}}
    if df.empty:
        return _empty

    # ── by_class: modal class per track ──────────────────────────────────────
    modal_cls = df.groupby("track_id")["class_id"].agg(lambda x: int(x.mode().iloc[0]))
    by_class: dict = {}
    for cls_id, cnt in modal_cls.value_counts().items():
        by_class[_NAMES.get(int(cls_id), f"class_{cls_id}")] = int(cnt)

    # ── avg track length (frames) ─────────────────────────────────────────────
    avg_frames = float(df.groupby("track_id").size().mean())

    # ── busiest zone via density grid ────────────────────────────────────────
    W = float(v.width or 1920)
    H = float(v.height or 1080)
    cell_w, cell_h = W / GRID_N, H / GRID_N
    col_arr = (df["cx"].to_numpy(dtype=np.float32) / cell_w).clip(0, GRID_N - 1).astype(int)
    row_arr = (df["cy"].to_numpy(dtype=np.float32) / cell_h).clip(0, GRID_N - 1).astype(int)
    cell_ids = row_arr * GRID_N + col_arr
    counts_arr = np.bincount(cell_ids, minlength=GRID_N * GRID_N)
    best = int(counts_arr.argmax())
    br, bc = divmod(best, GRID_N)
    busy_zone = {
        "cx_pct": (bc + 0.5) * cell_w / W,
        "cy_pct": (br + 0.5) * cell_h / H,
        "r_pct": max(cell_w, cell_h) * 0.6 / max(W, H),
    }

    # ── direction bins ────────────────────────────────────────────────────────
    agg = (
        df.sort_values("frame_idx")
        .groupby("track_id")
        .agg(
            cx_first=("cx", "first"), cy_first=("cy", "first"),
            cx_last=("cx", "last"),  cy_last=("cy", "last"),
        )
    )
    dx = (agg["cx_last"] - agg["cx_first"]).to_numpy(dtype=np.float32)
    dy = (agg["cy_last"] - agg["cy_first"]).to_numpy(dtype=np.float32)
    moving = np.hypot(dx, dy) > 5
    dx, dy = dx[moving], dy[moving]
    bins: dict = {"right": 0, "left": 0, "up": 0, "down": 0}
    for _dx, _dy in zip(dx.tolist(), dy.tolist()):
        if abs(_dx) >= abs(_dy):
            bins["right" if _dx > 0 else "left"] += 1
        else:
            bins["down" if _dy > 0 else "up"] += 1

    return {
        "total_tracks": int(df["track_id"].nunique()),
        "by_class": by_class,
        "busy_zone": busy_zone,
        "avg_track_frames": round(avg_frames, 1),
        "direction_bins": bins,
    }


@router.delete("/videos/{video_id}", status_code=204)
def delete_video(video_id: str, db: Session = Depends(get_db)):
    v = db.get(Video, video_id)
    if not v:
        raise HTTPException(404, "video not found")
    project_id = v.project_id
    vid = v.id
    db.delete(v)
    db.commit()
    try:
        get_storage().delete_prefix(f"projects/{project_id}/videos/{vid}")
    except Exception:
        pass
