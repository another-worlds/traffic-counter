from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TimestampRegionIn(BaseModel):
    x: int = Field(..., ge=0)
    y: int = Field(..., ge=0)
    w: int = Field(..., gt=0)
    h: int = Field(..., gt=0)
    source_frame_index: Optional[int] = None
    video_width: Optional[int] = None
    video_height: Optional[int] = None

    @field_validator(
        "x", "y", "w", "h", "source_frame_index", "video_width", "video_height",
        mode="before",
    )
    @classmethod
    def _coerce_int(cls, v: Any) -> Any:
        if v is None:
            return v
        return int(round(float(v)))


class TimestampRegionOut(BaseModel):
    model_config = ConfigDict(extra="ignore")

    x: int
    y: int
    w: int
    h: int
    confidence: float = 1.0
    method: str = "manual"
    video_width: Optional[int] = None
    video_height: Optional[int] = None
    area_percent: Optional[float] = None
    source_frame_index: Optional[int] = None
    processing_mode: str = "roi_only"


class TimestampScanRequest(BaseModel):
    force: bool = False
    region: Optional[TimestampRegionIn] = None


class CorrectedCountsRequest(BaseModel):
    line_ids: List[str] = Field(..., min_length=1)


class GapIntervalOut(BaseModel):
    start_frame: int
    end_frame: int
    start_t_s: float
    end_t_s: float
    reason: str


class TimestampProgressOut(BaseModel):
    phase: str
    phase_label: str
    percent: float
    message: Optional[str] = None


class TimestampMapOut(BaseModel):
    region: Optional[Dict[str, Any]] = None
    gaps: List[GapIntervalOut] = []
    stats: Dict[str, Any] = {}
    timeline_summary: Optional[Dict[str, Any]] = None
    timeline_viz: Optional[Dict[str, Any]] = None
    wall_clock_buckets: Optional[List[Dict[str, Any]]] = None
    num_segments: Optional[int] = None


class TimestampStatusOut(BaseModel):
    status: str
    artifacts: Dict[str, bool] = {}
    region: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None
    stats: Optional[Dict[str, Any]] = None
    progress: Optional[TimestampProgressOut] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    auto_scan_enabled: Optional[bool] = True
    cancel_requested: Optional[bool] = False


class LineDeltaOut(BaseModel):
    line_id: str
    line_name: str
    raw_total: int
    corrected_total: int
    excluded: int


class CorrectedCountsOut(BaseModel):
    gap_stats: Dict[str, Any]
    rows_excluded: int
    rows_excluded_fraction: float
    raw_counts: Dict[str, Any]
    corrected_counts: Dict[str, Any]
    delta_per_line: List[LineDeltaOut]