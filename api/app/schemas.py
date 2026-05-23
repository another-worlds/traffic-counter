from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional, List, Dict, Any


class ProjectCreate(BaseModel):
    name: str
    description: Optional[str] = None


class ProjectOut(BaseModel):
    id: str
    name: str
    description: Optional[str]
    # Set on workspaces auto-created from config/sources.yaml — the
    # absolute folder this workspace owns on disk. NULL for manually-
    # created workspaces.
    local_source_root: Optional[str] = None
    created_at: datetime
    last_exported_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class VideoOut(BaseModel):
    id: str
    project_id: str
    filename: str
    status: str
    size_bytes: Optional[int] = None
    fps: Optional[float]
    duration_s: Optional[float]
    width: Optional[int]
    height: Optional[int]
    num_frames: Optional[int]
    num_tracks: Optional[int]
    progress_pct: Optional[float] = None
    error_message: Optional[str]
    source: str = 'upload'
    local_source_path: Optional[str] = None
    created_at: datetime
    analyzed_at: Optional[datetime]
    started_analyzing_at: Optional[datetime] = None
    total_segments: Optional[int] = None
    segment_duration_s: Optional[float] = None

    class Config:
        from_attributes = True


class VideoSegmentOut(BaseModel):
    id: str
    video_id: str
    segment_idx: int
    status: str
    start_frame: int
    end_frame: int
    start_time_s: float
    end_time_s: float
    num_tracks: Optional[int] = None
    error_message: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    last_heartbeat_at: Optional[datetime] = None
    wall_clock_s: Optional[float] = None  # computed: completed_at − started_at

    class Config:
        from_attributes = True


class LineCreate(BaseModel):
    name: str
    # Two points in source-video pixel coords; client must scale from canvas to source.
    points: Dict[str, List[float]] = Field(
        ..., examples=[{"a": [100.0, 200.0], "b": [400.0, 200.0]}]
    )
    color: Optional[str] = "#e24b4a"


class LineOut(BaseModel):
    id: str
    video_id: str
    project_id: Optional[str] = None
    name: str
    points: Dict[str, List[float]]
    color: str
    created_at: datetime

    class Config:
        from_attributes = True


class CountRequest(BaseModel):
    # Single-video scope. Server validates that every line_id belongs to the
    # path-param video.
    line_ids: List[str]


class LineCountResult(BaseModel):
    line_id: str
    line_name: str
    total: int                      # unique tracks that crossed this line
    by_class: Dict[str, int]        # COCO class name -> count
    by_direction: Dict[str, int]    # "positive" / "negative"
    percent_of_video_total: float   # vs. total unique tracks in all selected videos
    percent_of_drawn_lines: float   # vs. sum across all selected lines


class CountResponse(BaseModel):
    total_unique_tracks: int        # T in (count/T)
    sum_across_lines: int           # Σ Cᵢ
    per_line: List[LineCountResult]


class WorkerVideoStatus(BaseModel):
    video_id: str
    project_id: str
    project_name: str
    filename: str
    status: str
    progress_pct: float
    started_analyzing_at: Optional[datetime]
    # Segment-aware progress fields (populated for segmented videos)
    current_segment_idx: Optional[int] = None
    total_segments: Optional[int] = None
    completed_segments: Optional[int] = None
    eta_seconds: Optional[float] = None
    speed_ratio: Optional[float] = None       # video-seconds per wall-clock-second
    worker_status_text: Optional[str] = None  # e.g. "Segment 3 of 12 (2:00–3:00)"


class WorkspaceSummary(BaseModel):
    project_id: str
    total_videos: int
    analyzed_videos: int
    queued_or_analyzing: int
    error_videos: int
    total_duration_s: Optional[float]
    total_size_bytes: Optional[int]
    lines_count: int
    last_exported_at: Optional[datetime]


class AnalyzeResponse(BaseModel):
    video_id: str
    status: str


class LineUpdate(BaseModel):
    name: Optional[str] = None
    color: Optional[str] = None
    points: Optional[Dict[str, List[float]]] = None


class SuggestLinesRequest(BaseModel):
    n: int = 3


class SuggestLineOut(BaseModel):
    name: str
    points: Dict[str, List[float]]
    color: str
    score: int  # number of tracks this line would cross
