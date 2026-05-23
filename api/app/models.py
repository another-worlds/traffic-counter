import uuid
from datetime import datetime
from sqlalchemy import Column, String, Text, Integer, Float, BigInteger, ForeignKey, DateTime, JSON, UniqueConstraint
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
    # Set by the YAML auto-sync to the absolute folder this workspace owns.
    # NULL for workspaces created manually through the UI.
    local_source_root = Column(String(1024), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_exported_at = Column(DateTime)

    videos = relationship("Video", back_populates="project", cascade="all, delete-orphan")
    # Lines now hang off videos, not projects; see Video.lines / CountingLine.video_id.


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
    # Bumped on every progress update so the API can detect abandoned claims.
    last_heartbeat_at = Column(DateTime, nullable=True, index=True)
    # Incremented by the reaper each time a claim goes stale. The reaper
    # requeues the row until this hits settings.max_analyze_attempts, then
    # finally marks it error so a permanently-bad video doesn't loop.
    analyze_attempts = Column(Integer, default=0, nullable=False, server_default="0")
    tus_upload_id = Column(String(64), nullable=True, index=True)

    # Scene-based keyframes extracted during analysis.
    # List of {"index": int, "time_s": float, "frame_index_in_video": int}
    scene_frames = Column(JSON, default=list, nullable=False, server_default="[]")

    # 'upload' (tus / direct) | 'local-folder' (watcher-imported, no copy)
    source = Column(String(32), default='upload', nullable=False)
    # Absolute host path for local-folder videos; worker reads directly from here.
    local_source_path = Column(String(1024), nullable=True)

    # Per-hour segmented processing.  NULL on old videos (not yet segmented).
    total_segments = Column(Integer, nullable=True)
    segment_duration_s = Column(Float, nullable=True)  # seconds per segment, default 3600

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    analyzed_at = Column(DateTime)

    project = relationship("Project", back_populates="videos")
    lines = relationship(
        "CountingLine", back_populates="video", cascade="all, delete-orphan"
    )
    segments = relationship(
        "VideoSegment", back_populates="video", cascade="all, delete-orphan",
        order_by="VideoSegment.segment_idx",
    )


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
    # Lines now belong to a specific video. The legacy project_id column
    # remains in the schema (nullable) for backwards-compatibility during
    # the migration window, but is no longer written by the API.
    project_id = Column(UUID(as_uuid=False), ForeignKey("projects.id", ondelete="CASCADE"), nullable=True)
    video_id = Column(
        UUID(as_uuid=False),
        ForeignKey("videos.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name = Column(String(255), nullable=False)
    # JSON: {"a": [x, y], "b": [x, y]} in source-video pixel coordinates
    points = Column(JSON, nullable=False)
    color = Column(String(16), default="#e24b4a")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    video = relationship("Video", back_populates="lines")


class VideoSegment(Base):
    """One hour-sized processing chunk of a video.

    The worker processes each segment independently with a fresh ByteTrack
    instance and writes a per-segment parquet.  On any restart the worker
    skips segments whose status is already 'done' and resumes from the
    first pending one — giving crash-safe, docker-compose-restart-safe
    checkpointing with no special shutdown logic required.
    """
    __tablename__ = "video_segments"
    __table_args__ = (
        UniqueConstraint("video_id", "segment_idx", name="uq_video_segments_video_idx"),
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    video_id = Column(
        UUID(as_uuid=False),
        ForeignKey("videos.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    segment_idx = Column(Integer, nullable=False)  # 0-based

    # pending → analyzing → done | error
    status = Column(String(32), nullable=False, default="pending")

    start_frame = Column(Integer, nullable=False)
    end_frame = Column(Integer, nullable=False)
    start_time_s = Column(Float, nullable=False)
    end_time_s = Column(Float, nullable=False)

    num_tracks = Column(Integer, nullable=True)
    error_message = Column(Text, nullable=True)

    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    last_heartbeat_at = Column(DateTime, nullable=True)

    video = relationship("Video", back_populates="segments")
