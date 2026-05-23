from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from ..db import get_db
from ..models import Video, CountingLine
from ..schemas import LineCreate, LineOut, LineUpdate

router = APIRouter(tags=["lines"])


@router.get("/videos/{video_id}/lines", response_model=List[LineOut])
def list_lines(video_id: str, db: Session = Depends(get_db)):
    if not db.get(Video, video_id):
        raise HTTPException(404, "video not found")
    return (
        db.query(CountingLine)
        .filter(CountingLine.video_id == video_id)
        .order_by(CountingLine.created_at.asc())
        .all()
    )


@router.post("/videos/{video_id}/lines", response_model=LineOut, status_code=201)
def create_line(video_id: str, payload: LineCreate, db: Session = Depends(get_db)):
    v = db.get(Video, video_id)
    if not v:
        raise HTTPException(404, "video not found")
    if "a" not in payload.points or "b" not in payload.points:
        raise HTTPException(422, "points must have keys 'a' and 'b'")
    line = CountingLine(
        video_id=video_id,
        project_id=v.project_id,  # kept for legacy reads; not authoritative
        name=payload.name,
        points=payload.points,
        color=payload.color or "#e24b4a",
    )
    db.add(line)
    db.commit()
    db.refresh(line)
    return line


@router.delete("/lines/{line_id}", status_code=204)
def delete_line(line_id: str, db: Session = Depends(get_db)):
    ln = db.get(CountingLine, line_id)
    if not ln:
        raise HTTPException(404, "line not found")
    db.delete(ln)
    db.commit()


@router.patch("/lines/{line_id}", response_model=LineOut)
def update_line(line_id: str, payload: LineUpdate, db: Session = Depends(get_db)):
    ln = db.get(CountingLine, line_id)
    if not ln:
        raise HTTPException(404, "line not found")
    if payload.name is not None:
        ln.name = payload.name
    if payload.color is not None:
        ln.color = payload.color
    if payload.points is not None:
        if "a" not in payload.points or "b" not in payload.points:
            raise HTTPException(422, "points must have keys 'a' and 'b'")
        ln.points = payload.points
    db.commit()
    db.refresh(ln)
    return ln
