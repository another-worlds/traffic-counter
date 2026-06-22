from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel


# --- scenarios ---
class ScenarioCreate(BaseModel):
    name: str
    description: str = ""
    bbox: Optional[List[float]] = None  # [south, west, north, east]


class ScenarioOut(BaseModel):
    id: str
    name: str
    description: Optional[str] = ""
    bbox: Optional[List[float]] = None
    created_at: datetime
    n_nodes: int = 0
    n_links: int = 0
    n_zones: int = 0
    n_counters: int = 0


# --- network ---
class BBoxImport(BaseModel):
    south: float
    west: float
    north: float
    east: float


class SampleNetworkRequest(BaseModel):
    rows: int = 4
    cols: int = 4
    lat0: float = 41.30
    lon0: float = 69.26
    spacing_m: float = 300.0
    free_flow_speed_ms: float = 13.9
    lanes: int = 1


# --- zones ---
class ZoneCreate(BaseModel):
    name: str
    centroid: Optional[Dict[str, Any]] = None  # GeoJSON Point
    geom: Optional[Dict[str, Any]] = None
    production: float = 0.0
    attraction: float = 0.0


class ZoneUpdate(BaseModel):
    name: Optional[str] = None
    production: Optional[float] = None
    attraction: Optional[float] = None


class AutoZonesRequest(BaseModel):
    n: int = 8  # number of zones to auto-place on perimeter/grid nodes


# --- counters ---
class CounterCreate(BaseModel):
    name: str
    lat: float
    lon: float
    source_video_id: Optional[str] = None
    source_line_id: Optional[str] = None
    link_direction: str = "AB"
    # optional: directly provide an observed volume (used by the demo seeder / manual entry)
    observed_vph: Optional[float] = None
    pcu_vph: Optional[float] = None


# --- model steps ---
class DistributionRequest(BaseModel):
    beta: float = 0.1  # gravity deterrence (per minute of travel time)


class AssignmentRequest(BaseModel):
    od_matrix_id: Optional[str] = None


class RunFourStepRequest(BaseModel):
    beta: float = 0.1


class CalibrateRequest(BaseModel):
    max_iters: Optional[int] = None


# --- results ---
class SummaryOut(BaseModel):
    scenario_id: str
    n_links: int
    n_counters: int
    mean_geh: Optional[float] = None
    pct_geh_lt5: Optional[float] = None
    rmse: Optional[float] = None
    total_vkt: Optional[float] = None
