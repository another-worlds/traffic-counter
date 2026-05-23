import asyncio
from collections import defaultdict

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from typing import List

from ..db import get_db
from ..models import Video, CountingLine
from ..schemas import (
    CountRequest,
    CountResponse,
    LineCountResult,
    SuggestLinesRequest,
    SuggestLineOut,
)
from ..services.tracks import load_materialized_tracks, load_tracks_for_video
from ..services.counting import compute_counts_for_lines
from ..services.suggest import suggest_lines as _suggest_lines
from ..services import xlsx_jobs

router = APIRouter(tags=["analysis"])


def _lines_to_dict(lines: List[CountingLine]) -> list:
    return [
        {
            "id": ln.id,
            "name": ln.name,
            "a": ln.points["a"],
            "b": ln.points["b"],
        }
        for ln in lines
    ]


def _load_lines_for_video(db: Session, video_id: str, line_ids: List[str]) -> List[CountingLine]:
    lines = (
        db.query(CountingLine)
        .filter(
            CountingLine.video_id == video_id,
            CountingLine.id.in_(line_ids),
        )
        .all()
    )
    if len(lines) != len(line_ids):
        raise HTTPException(400, "one or more line_ids do not belong to this video")
    return lines


# One asyncio lock per video. When a user draws lines quickly, the
# frontend coalesces /counts requests but two clients (or a stale
# in-flight request that hasn't aborted yet) can still race. The lock
# keeps N concurrent requests for the same video from each loading the
# parquet on a cold cache and doubling peak memory.
_recompute_locks: "dict[str, asyncio.Lock]" = defaultdict(asyncio.Lock)


@router.post("/videos/{video_id}/counts", response_model=CountResponse)
async def counts(video_id: str, body: CountRequest, db: Session = Depends(get_db)):
    async with _recompute_locks[video_id]:
        v = db.get(Video, video_id)
        if not v:
            raise HTTPException(404, "video not found")
        if v.status != "analyzed":
            raise HTTPException(409, "video must be analyzed first")

        lines = _load_lines_for_video(db, video_id, body.line_ids)
        # MaterializedTracks: sort + groupby + modal-class precomputed once per
        # video, shared across every line in this request and every subsequent
        # request for the same video until re-analysis bumps the cache key.
        mt = load_materialized_tracks(v.project_id, video_id)
        result = compute_counts_for_lines(mt, _lines_to_dict(lines))

        return CountResponse(
            total_unique_tracks=result["total_unique_tracks"],
            sum_across_lines=result["sum_across_lines"],
            per_line=[LineCountResult(**r) for r in result["per_line"]],
        )


@router.post("/videos/{video_id}/export", status_code=202)
def export_start(
    video_id: str,
    body: CountRequest,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Kick off an async xlsx build. Returns a job id the client polls."""
    v = db.get(Video, video_id)
    if not v:
        raise HTTPException(404, "video not found")
    if not body.line_ids:
        raise HTTPException(400, "must select at least one line")
    if v.status != "analyzed":
        raise HTTPException(409, "video must be analyzed first")

    # Validate lines belong to the video before scheduling work.
    _load_lines_for_video(db, video_id, body.line_ids)

    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in v.filename)
    project_name = v.project.name if v.project else "project"
    job = xlsx_jobs.start_job(
        video_id=video_id,
        filename=f"counts-{safe_name}.xlsx",
    )
    background.add_task(
        xlsx_jobs.run_export_job,
        job_id=job.job_id,
        project_id=str(v.project_id),
        project_name=project_name,
        video_id=video_id,
        video_filename=v.filename,
        line_ids=body.line_ids,
    )
    return {"job_id": job.job_id, "status": job.status}


@router.get("/export-jobs/{job_id}")
def export_status(job_id: str):
    j = xlsx_jobs.get(job_id)
    if not j:
        raise HTTPException(404, "job not found")
    return {
        "job_id": j.job_id,
        "video_id": j.video_id,
        "status": j.status,
        "error": j.error,
        "filename": j.filename,
    }


@router.get("/export-jobs/{job_id}/file")
def export_file(job_id: str):
    j = xlsx_jobs.get(job_id)
    if not j or j.status != "done" or not j.file_path:
        raise HTTPException(404, "file not ready")
    return FileResponse(
        j.file_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=j.filename,
    )


@router.post("/videos/{video_id}/suggest-lines", response_model=List[SuggestLineOut])
def suggest_lines(video_id: str, body: SuggestLinesRequest, db: Session = Depends(get_db)):
    """Return up to *n* automatically placed counting-line suggestions for one video."""
    v = db.get(Video, video_id)
    if not v:
        raise HTTPException(404, "video not found")
    if v.status != "analyzed":
        raise HTTPException(409, "video must be analyzed first")

    tracks = load_tracks_for_video(v.project_id, video_id)
    w = v.width or 1920
    h = v.height or 1080
    suggestions = _suggest_lines(tracks, w, h, n=body.n)
    return [SuggestLineOut(**s) for s in suggestions]
