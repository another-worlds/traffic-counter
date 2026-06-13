"""The data contract: typed request/response models (Pydantic v2).

Geometry crosses the API boundary as **GeoJSON** geometry objects (RFC 7946) — a plain
``{"type": ..., "coordinates": ...}`` dict — and is stored natively in PostGIS. Read
models carry the geometry back as GeoJSON so the round-trip is lossless.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

# A GeoJSON geometry object. Kept as a dict (validated structurally on ingest via
# shapely) rather than a closed union, so any RFC-7946 geometry is accepted.
GeoJSON = dict[str, Any]


def _is_geometry(g: GeoJSON) -> bool:
    return isinstance(g, dict) and "type" in g and "coordinates" in g


# --------------------------------------------------------------------------- #
# Scenario                                                                    #
# --------------------------------------------------------------------------- #
class ScenarioCreate(BaseModel):
    name: str
    description: str = ""
    bbox: list[float] | None = None  # [south, west, north, east]
    parent_id: str | None = None


class ScenarioRead(BaseModel):
    id: str
    name: str
    description: str = ""
    bbox: list[float] | None = None
    parent_id: str | None = None
    created_at: datetime | None = None


# --------------------------------------------------------------------------- #
# Nodes                                                                       #
# --------------------------------------------------------------------------- #
class NodeCreate(BaseModel):
    name: str | None = None
    geometry: GeoJSON  # Point
    osm_id: str | None = None
    node_type_id: str | None = None


class NodeRead(BaseModel):
    id: str
    scenario_id: str
    name: str | None = None
    geometry: GeoJSON | None = None
    osm_id: str | None = None
    node_type_id: str | None = None


# --------------------------------------------------------------------------- #
# Links                                                                       #
# --------------------------------------------------------------------------- #
class LinkCreate(BaseModel):
    name: str | None = None
    from_node_id: str
    to_node_id: str
    # Optional: if omitted, a straight line between the two nodes is generated and
    # the geodesic length computed server-side.
    geometry: GeoJSON | None = None
    lanes: int = 1
    free_speed_kmh: float = 50.0
    capacity_vph: float | None = None
    oneway: bool = True
    allowed_modes: list[str] | None = None
    osm_id: str | None = None
    link_type_id: str | None = None


class LinkRead(BaseModel):
    id: str
    scenario_id: str
    name: str | None = None
    from_node_id: str | None = None
    to_node_id: str | None = None
    geometry: GeoJSON | None = None
    length_m: float = 0.0
    lanes: int = 1
    free_speed_kmh: float = 50.0
    capacity_vph: float | None = None
    oneway: bool = True
    allowed_modes: list[str] | None = None
    link_type_id: str | None = None


# --------------------------------------------------------------------------- #
# Zones                                                                       #
# --------------------------------------------------------------------------- #
class ZoneCreate(BaseModel):
    name: str | None = None
    geometry: GeoJSON | None = None   # Polygon boundary (optional)
    centroid: GeoJSON | None = None   # Point (optional; defaults to polygon centroid)
    population: float = 0.0
    workplaces: float = 0.0
    zone_type_id: str | None = None


class ZoneRead(BaseModel):
    id: str
    scenario_id: str
    name: str | None = None
    geometry: GeoJSON | None = None
    centroid: GeoJSON | None = None
    population: float = 0.0
    workplaces: float = 0.0
    zone_type_id: str | None = None


# --------------------------------------------------------------------------- #
# Counters                                                                     #
# --------------------------------------------------------------------------- #
class CounterCreate(BaseModel):
    name: str | None = None
    geometry: GeoJSON  # Point
    snapped_link_id: str | None = None
    link_direction: Literal["AB", "BA"] = "AB"
    source_video_id: str | None = None
    source_line_id: str | None = None
    observed_vph: float | None = None


class CounterRead(BaseModel):
    id: str
    scenario_id: str
    name: str | None = None
    geometry: GeoJSON | None = None
    snapped_link_id: str | None = None
    link_direction: str = "AB"
    source_video_id: str | None = None
    source_line_id: str | None = None
    observed_vph: float | None = None


# --------------------------------------------------------------------------- #
# KNN snap (the load-bearing spatial capability)                              #
# --------------------------------------------------------------------------- #
class SnapRequest(BaseModel):
    lon: float = Field(..., description="WGS84 longitude")
    lat: float = Field(..., description="WGS84 latitude")
    max_candidates: int = 5  # nearest-by-GiST candidates re-ranked by geodesic distance


class SnapResult(BaseModel):
    link_id: str
    name: str | None = None
    distance_m: float


# --------------------------------------------------------------------------- #
# GeoJSON FeatureCollection (the /network export)                             #
# --------------------------------------------------------------------------- #
class Feature(BaseModel):
    type: Literal["Feature"] = "Feature"
    id: str
    geometry: GeoJSON | None = None
    properties: dict[str, Any] = Field(default_factory=dict)


class FeatureCollection(BaseModel):
    type: Literal["FeatureCollection"] = "FeatureCollection"
    features: list[Feature] = Field(default_factory=list)
