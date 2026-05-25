from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List

from ..db import get_db
from ..models import Project, Video, CountingLine
from ..schemas import ProjectCreate, ProjectOut, WorkspaceSummary

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("", response_model=List[ProjectOut])
def list_projects(limit: int = 200, offset: int = 0, db: Session = Depends(get_db)):
    return (
        db.query(Project)
        .order_by(Project.created_at.desc())
        .limit(limit)
        .offset(offset)
        .all()
    )


@router.post("", response_model=ProjectOut, status_code=201)
def create_project(payload: ProjectCreate, db: Session = Depends(get_db)):
    p = Project(name=payload.name, description=payload.description)
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(project_id: str, db: Session = Depends(get_db)):
    p = db.get(Project, project_id)
    if not p:
        raise HTTPException(404, "project not found")
    return p


@router.get("/{project_id}/summary", response_model=WorkspaceSummary)
def workspace_summary(project_id: str, db: Session = Depends(get_db)):
    """Single-query aggregate stats for a workspace."""
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "project not found")

    from sqlalchemy import case
    stats = (
        db.query(
            func.count(Video.id).label("total"),
            func.sum(case((Video.status == "analyzed", 1), else_=0)).label("analyzed"),
            func.sum(case((Video.status.in_(["queued", "analyzing"]), 1), else_=0)).label("active"),
            func.sum(case((Video.status == "error", 1), else_=0)).label("errors"),
            func.sum(Video.duration_s).label("total_duration_s"),
            func.sum(Video.size_bytes).label("total_size_bytes"),
        )
        .filter(Video.project_id == project_id)
        .first()
    )

    lines_count = (
        db.query(func.count(CountingLine.id))
        .join(Video, CountingLine.video_id == Video.id)
        .filter(Video.project_id == project_id)
        .scalar()
    )

    return WorkspaceSummary(
        project_id=project_id,
        total_videos=stats.total or 0,
        analyzed_videos=stats.analyzed or 0,
        queued_or_analyzing=stats.active or 0,
        error_videos=stats.errors or 0,
        total_duration_s=stats.total_duration_s,
        total_size_bytes=stats.total_size_bytes,
        lines_count=lines_count or 0,
        last_exported_at=project.last_exported_at,
    )
