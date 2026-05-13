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

    class Config:
        from_attributes = True


class VideoOut(BaseModel):
    id: str
    project_id: str
    filename: str
    status: str
    fps: Optional[float]
    duration_s: Optional[float]
    width: Optional[int]
    height: Optional[int]
    num_frames: Optional[int]
    num_tracks: Optional[int]
    error_message: Optional[str]
    created_at: datetime
    analyzed_at: Optional[datetime]

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


class AnalyzeResponse(BaseModel):
    video_id: str
    status: str
