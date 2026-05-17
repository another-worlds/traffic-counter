import uuid
from datetime import datetime
from sqlalchemy import Column, String, Text, Integer, Float, BigInteger, ForeignKey, DateTime, JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from .db import Base


def gen_uuid() -> str:
    return str(uuid.uuid4())


class Project(Base):
    __tablename__ = "projects"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    name = Column(String(255), nullable=False)
    description = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_exported_at = Column(DateTime)

    videos = relationship("Video", back_populates="project", cascade="all, delete-orphan")
    lines = relationship("CountingLine", back_populates="project", cascade="all, delete-orphan")


class Video(Base):
    __tablename__ = "videos"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    project_id = Column(UUID(as_uuid=False), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    filename = Column(String(512), nullable=False)
    storage_path = Column(String(1024), nullable=False)

    # uploaded -> queued -> analyzing -> analyzed | error
    status = Column(String(32), default="uploaded", nullable=False)
    error_message = Column(Text)

    size_bytes = Column(BigInteger)
    fps = Column(Float)
    duration_s = Column(Float)
    width = Column(Integer)
    height = Column(Integer)
    num_frames = Column(Integer)
    num_tracks = Column(Integer)

    # Analysis progress: 0.0–1.0 while analyzing, reset to None when done/error
    progress_pct = Column(Float, default=0.0)
    started_analyzing_at = Column(DateTime)
    tus_upload_id = Column(String(64), nullable=True, index=True)

    # Scene-based keyframes extracted during analysis.
    # List of {"index": int, "time_s": float, "frame_index_in_video": int}
    scene_frames = Column(JSON, default=list, nullable=False, server_default="[]")

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    analyzed_at = Column(DateTime)

    project = relationship("Project", back_populates="videos")


class TusUpload(Base):
    """Temporary record tracking an in-progress tus resumable upload."""
    __tablename__ = "tus_uploads"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    project_id = Column(UUID(as_uuid=False), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    video_id = Column(UUID(as_uuid=False), ForeignKey("videos.id", ondelete="SET NULL"), nullable=True)
    filename = Column(String(512), nullable=False)
    upload_length = Column(BigInteger, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class CountingLine(Base):
    __tablename__ = "counting_lines"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    project_id = Column(UUID(as_uuid=False), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(255), nullable=False)
    # JSON: {"a": [x, y], "b": [x, y]} in source-video pixel coordinates
    points = Column(JSON, nullable=False)
    color = Column(String(16), default="#e24b4a")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    project = relationship("Project", back_populates="lines")
