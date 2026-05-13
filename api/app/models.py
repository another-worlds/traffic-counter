import uuid
from datetime import datetime
from sqlalchemy import Column, String, Text, Integer, Float, ForeignKey, DateTime, JSON
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

    fps = Column(Float)
    duration_s = Column(Float)
    width = Column(Integer)
    height = Column(Integer)
    num_frames = Column(Integer)
    num_tracks = Column(Integer)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    analyzed_at = Column(DateTime)

    project = relationship("Project", back_populates="videos")


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
