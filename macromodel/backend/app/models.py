"""ORM models for the macroscopic model.

Geometry is stored as GeoJSON geometry dicts in JSON columns (MVP simplification);
spatial operations are done in shapely/networkx. Native PostGIS geometry columns
via GeoAlchemy2 are the documented upgrade path.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from .db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class Scenario(Base):
    __tablename__ = "scenarios"

    id = Column(String, primary_key=True, default=_uuid)
    name = Column(String(255), nullable=False)
    description = Column(Text, default="")
    bbox = Column(JSON, nullable=True)  # [south, west, north, east]
    created_at = Column(DateTime, default=datetime.utcnow)

    nodes = relationship("Node", back_populates="scenario", cascade="all, delete-orphan")
    links = relationship("Link", back_populates="scenario", cascade="all, delete-orphan")
    zones = relationship("Zone", back_populates="scenario", cascade="all, delete-orphan")
    counters = relationship("Counter", back_populates="scenario", cascade="all, delete-orphan")


class Node(Base):
    __tablename__ = "nodes"

    id = Column(String, primary_key=True, default=_uuid)
    scenario_id = Column(String, ForeignKey("scenarios.id", ondelete="CASCADE"), index=True)
    name = Column(String(128))
    geom = Column(JSON)  # GeoJSON Point: {"type":"Point","coordinates":[lon,lat]}
    osm_id = Column(String(64), nullable=True)

    scenario = relationship("Scenario", back_populates="nodes")


class Link(Base):
    """A directed road link (A->B). A two-way street is two Link rows."""

    __tablename__ = "links"

    id = Column(String, primary_key=True, default=_uuid)
    scenario_id = Column(String, ForeignKey("scenarios.id", ondelete="CASCADE"), index=True)
    name = Column(String(128))
    from_node_id = Column(String, ForeignKey("nodes.id", ondelete="CASCADE"))
    to_node_id = Column(String, ForeignKey("nodes.id", ondelete="CASCADE"))
    geom = Column(JSON)  # GeoJSON LineString
    length_m = Column(Float, default=0.0)
    lanes = Column(Integer, default=1)
    free_flow_speed_ms = Column(Float, default=13.9)  # ~50 km/h
    jam_density = Column(Float, default=0.2)  # veh/m
    capacity_vph = Column(Float, nullable=True)
    oneway = Column(Boolean, default=False)
    osm_id = Column(String(64), nullable=True)

    scenario = relationship("Scenario", back_populates="links")


class Zone(Base):
    """Traffic Analysis Zone (TAZ)."""

    __tablename__ = "zones"

    id = Column(String, primary_key=True, default=_uuid)
    scenario_id = Column(String, ForeignKey("scenarios.id", ondelete="CASCADE"), index=True)
    name = Column(String(128))
    geom = Column(JSON, nullable=True)  # GeoJSON Polygon (optional for MVP)
    centroid = Column(JSON, nullable=True)  # GeoJSON Point
    connector_node_id = Column(String, ForeignKey("nodes.id", ondelete="SET NULL"), nullable=True)
    production = Column(Float, default=0.0)
    attraction = Column(Float, default=0.0)

    scenario = relationship("Scenario", back_populates="zones")


class Counter(Base):
    """A georeferenced traffic counter, bridging a traffic-counter counting line to a link."""

    __tablename__ = "counters"

    id = Column(String, primary_key=True, default=_uuid)
    scenario_id = Column(String, ForeignKey("scenarios.id", ondelete="CASCADE"), index=True)
    name = Column(String(128))
    geom = Column(JSON)  # GeoJSON Point where the user dropped it
    snapped_link_id = Column(String, ForeignKey("links.id", ondelete="SET NULL"), nullable=True)
    link_direction = Column(String(2), default="AB")  # which link direction the line's +ve maps to

    # provenance in the traffic-counter app
    source_video_id = Column(String(64), nullable=True)
    source_line_id = Column(String(64), nullable=True)

    # measured target (filled by pull-observations or the demo seeder)
    observed_vph = Column(Float, nullable=True)
    pcu_vph = Column(Float, nullable=True)
    hours = Column(Float, nullable=True)

    scenario = relationship("Scenario", back_populates="counters")


class Observation(Base):
    __tablename__ = "observations"

    id = Column(String, primary_key=True, default=_uuid)
    counter_id = Column(String, ForeignKey("counters.id", ondelete="CASCADE"), index=True)
    link_id = Column(String, nullable=True)
    vehicle_class = Column(String(32))
    direction = Column(String(16))
    count_total = Column(Float)
    hours = Column(Float)
    vph = Column(Float)


class ODMatrix(Base):
    __tablename__ = "od_matrices"

    id = Column(String, primary_key=True, default=_uuid)
    scenario_id = Column(String, ForeignKey("scenarios.id", ondelete="CASCADE"), index=True)
    name = Column(String(128))
    step = Column(String(32))  # seed | distributed | calibrated
    n_zones = Column(Integer)
    storage_ref = Column(String(256))  # parquet key in storage_root
    created_at = Column(DateTime, default=datetime.utcnow)


class AssignmentResult(Base):
    __tablename__ = "assignment_results"

    id = Column(String, primary_key=True, default=_uuid)
    scenario_id = Column(String, ForeignKey("scenarios.id", ondelete="CASCADE"), index=True)
    od_matrix_id = Column(String, nullable=True)
    storage_ref = Column(String(256))  # parquet key: per-link sim volumes
    mean_geh = Column(Float, nullable=True)
    rmse = Column(Float, nullable=True)
    total_vkt = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class CalibrationRun(Base):
    __tablename__ = "calibration_runs"

    id = Column(String, primary_key=True, default=_uuid)
    scenario_id = Column(String, ForeignKey("scenarios.id", ondelete="CASCADE"), index=True)
    method = Column(String(64), default="path_multiplicative_odme")
    iterations = Column(Integer)
    od_matrix_id = Column(String, nullable=True)
    mean_geh = Column(Float, nullable=True)
    pct_geh_lt5 = Column(Float, nullable=True)
    converged = Column(Boolean, default=False)
    history = Column(JSON, nullable=True)  # per-iteration metrics
    created_at = Column(DateTime, default=datetime.utcnow)
