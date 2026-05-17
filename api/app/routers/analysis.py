from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from typing import List
from datetime import datetime
import io
import uuid

from ..db import get_db
from ..models import Project, Video, CountingLine
from ..schemas import CountRequest, CountResponse, LineCountResult, SuggestLinesRequest, SuggestLineOut
from ..services.tracks import load_tracks_for_videos
from ..services.counting import compute_counts_for_lines
from ..services.xlsx_export import build_xlsx
from ..services.suggest import suggest_lines as _suggest_lines

router = APIRouter(tags=["analysis"])


def _lines_to_dict(lines: List[CountingLine]) -> list:
    out = []
    for ln in lines:
        out.append({
            "id": ln.id,
            "name": ln.name,
            "a": ln.points["a"],
            "b": ln.points["b"],
        })
    return out


@router.post("/projects/{project_id}/counts", response_model=CountResponse)
def counts(project_id: str, body: CountRequest, db: Session = Depends(get_db)):
    if not db.get(Project, project_id):
        raise HTTPException(404, "project not found")

    videos = (
        db.query(Video)
        .filter(Video.project_id == project_id, Video.id.in_(body.video_ids))
        .all()
    )
    if len(videos) != len(body.video_ids):
        raise HTTPException(400, "one or more video_ids do not belong to this project")
    if any(v.status != "analyzed" for v in videos):
        raise HTTPException(409, "all selected videos must be analyzed first")

    lines = (
        db.query(CountingLine)
        .filter(
            CountingLine.project_id == project_id,
            CountingLine.id.in_(body.line_ids),
        )
        .all()
    )
    if len(lines) != len(body.line_ids):
        raise HTTPException(400, "one or more line_ids do not belong to this project")

    tracks = load_tracks_for_videos(project_id, [v.id for v in videos])
    result = compute_counts_for_lines(tracks, _lines_to_dict(lines))

    return CountResponse(
        total_unique_tracks=result["total_unique_tracks"],
        sum_across_lines=result["sum_across_lines"],
        per_line=[LineCountResult(**r) for r in result["per_line"]],
    )


@router.post("/projects/{project_id}/export")
def export(project_id: str, body: CountRequest, db: Session = Depends(get_db)):
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "project not found")

    videos = (
        db.query(Video)
        .filter(Video.project_id == project_id, Video.id.in_(body.video_ids))
        .all()
    )
    lines = (
        db.query(CountingLine)
        .filter(
            CountingLine.project_id == project_id,
            CountingLine.id.in_(body.line_ids),
        )
        .all()
    )
    if not videos or not lines:
        raise HTTPException(400, "must select at least one video and one line")

    video_rows = [{"id": v.id, "filename": v.filename} for v in videos]
    data = build_xlsx(project_id, project.name, video_rows, _lines_to_dict(lines))

    project.last_exported_at = datetime.utcnow()
    db.commit()

    fname = f"counts-{project.name.replace(' ', '_')}-{uuid.uuid4().hex[:6]}.xlsx"
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.post("/projects/{project_id}/suggest-lines", response_model=List[SuggestLineOut])
def suggest_lines(project_id: str, body: SuggestLinesRequest, db: Session = Depends(get_db)):
    """Return up to *n* automatically placed counting-line suggestions."""
    if not db.get(Project, project_id):
        raise HTTPException(404, "project not found")

    videos = (
        db.query(Video)
        .filter(Video.project_id == project_id, Video.id.in_(body.video_ids))
        .all()
    )
    if len(videos) != len(body.video_ids):
        raise HTTPException(400, "one or more video_ids do not belong to this project")
    if any(v.status != "analyzed" for v in videos):
        raise HTTPException(409, "all selected videos must be analyzed first")

    tracks = load_tracks_for_videos(project_id, [v.id for v in videos])

    # Use dimensions from the first video; fall back to 1920×1080 if not set.
    ref = videos[0]
    w = ref.width or 1920
    h = ref.height or 1080

    suggestions = _suggest_lines(tracks, w, h, n=body.n)
    return [SuggestLineOut(**s) for s in suggestions]
