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
    progress_updated_at: Optional[datetime] = None
    retries: int = 0
    created_at: datetime
    analyzed_at: Optional[datetime]
    started_analyzing_at: Optional[datetime] = None

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
    project_id: str
    name: str
    points: Dict[str, List[float]]
    color: str
    created_at: datetime

    class Config:
        from_attributes = True


class CountRequest(BaseModel):
    video_ids: List[str]
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
    video_ids: List[str]
    n: int = 3


class SuggestLineOut(BaseModel):
    name: str
    points: Dict[str, List[float]]
    color: str
    score: int  # number of tracks this line would cross


class PauseStateOut(BaseModel):
    paused: bool


class DashboardAnalyzing(BaseModel):
    id: str
    filename: str
    progress_pct: float
    started_at: Optional[datetime]
    eta_s: Optional[float]


class DashboardError(BaseModel):
    id: str
    filename: str
    error_message: Optional[str]
    retries: int


class DashboardCounts(BaseModel):
    total: int
    uploaded: int
    queued: int
    analyzing: int
    analyzed: int
    error: int


class LocalFolderDashboard(BaseModel):
    paused: bool
    counts: DashboardCounts
    currently_analyzing: List[DashboardAnalyzing]
    throughput_per_hour: float
    avg_analysis_seconds: Optional[float]
    queue_eta_seconds: Optional[float]
    recent_errors: List[DashboardError]


class RetryErrorsResponse(BaseModel):
    queued: int
