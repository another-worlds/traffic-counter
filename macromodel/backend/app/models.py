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
    node_type_id = Column(String, ForeignKey("node_types.id", ondelete="SET NULL"), nullable=True)

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
    link_type_id = Column(String, ForeignKey("link_types.id", ondelete="SET NULL"), nullable=True)
    v0_kmh = Column(Float, nullable=True)  # free-flow speed override (km/h); else from type/m_s
    allowed_modes = Column(JSON, nullable=True)  # e.g. ["PrT","PuT"]; null → all

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
    production = Column(Float, default=0.0)  # legacy total; per-activity lives in ZoneDemand
    attraction = Column(Float, default=0.0)
    # demand-strata variables that drive generation (req #5)
    population = Column(Float, default=0.0)
    workplaces = Column(Float, default=0.0)
    zone_type_id = Column(String, ForeignKey("zone_types.id", ondelete="SET NULL"), nullable=True)

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
    step = Column(String(32))  # seed | distributed | calibrated | skim | loaded
    n_zones = Column(Integer)
    storage_ref = Column(String(256))  # parquet key in storage_root
    kind = Column(String(16), default="demand")  # demand | skim
    mode_id = Column(String, nullable=True)
    demand_layer_id = Column(String, nullable=True)
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


# --------------------------------------------------------------------------- #
# Classes (Visum-style typing of network objects)                             #
# --------------------------------------------------------------------------- #
class LinkType(Base):
    __tablename__ = "link_types"

    id = Column(String, primary_key=True, default=_uuid)
    scenario_id = Column(String, ForeignKey("scenarios.id", ondelete="CASCADE"), index=True)
    name = Column(String(128))
    rank = Column(Integer, default=3)            # 1=major … higher=minor
    num_lanes = Column(Integer, default=1)
    capacity_vph = Column(Float, default=1800.0)  # per lane
    v0_kmh = Column(Float, default=50.0)
    allowed_modes = Column(JSON, default=lambda: ["PrT", "PuT"])


class NodeType(Base):
    """Crossroad control class (attribute only — no signal timing in the MVP)."""

    __tablename__ = "node_types"

    id = Column(String, primary_key=True, default=_uuid)
    scenario_id = Column(String, ForeignKey("scenarios.id", ondelete="CASCADE"), index=True)
    name = Column(String(128))
    control = Column(String(32), default="uncontrolled")  # uncontrolled|signalized|roundabout|twoway_stop


class ZoneType(Base):
    __tablename__ = "zone_types"

    id = Column(String, primary_key=True, default=_uuid)
    scenario_id = Column(String, ForeignKey("scenarios.id", ondelete="CASCADE"), index=True)
    name = Column(String(128))
    category = Column(String(32), default="mixed")  # residential|employment|mixed


# --------------------------------------------------------------------------- #
# Network objects                                                             #
# --------------------------------------------------------------------------- #
class Connector(Base):
    """Zone <-> node access link."""

    __tablename__ = "connectors"

    id = Column(String, primary_key=True, default=_uuid)
    scenario_id = Column(String, ForeignKey("scenarios.id", ondelete="CASCADE"), index=True)
    zone_id = Column(String, ForeignKey("zones.id", ondelete="CASCADE"))
    node_id = Column(String, ForeignKey("nodes.id", ondelete="CASCADE"))
    direction = Column(String(12), default="both")  # origin|destination|both
    t0_min = Column(Float, default=0.5)
    weight = Column(Float, default=1.0)


class Stop(Base):
    """PuT stop point."""

    __tablename__ = "stops"

    id = Column(String, primary_key=True, default=_uuid)
    scenario_id = Column(String, ForeignKey("scenarios.id", ondelete="CASCADE"), index=True)
    name = Column(String(128))
    geom = Column(JSON)  # GeoJSON Point
    node_id = Column(String, ForeignKey("nodes.id", ondelete="SET NULL"), nullable=True)


class Line(Base):
    """PuT line (with an ordered stop sequence in LineRouteStop)."""

    __tablename__ = "lines"

    id = Column(String, primary_key=True, default=_uuid)
    scenario_id = Column(String, ForeignKey("scenarios.id", ondelete="CASCADE"), index=True)
    name = Column(String(128))
    tsys = Column(String(32), default="Bus")  # Bus|Tram|...
    headway_min = Column(Float, default=10.0)
    color = Column(String(16), default="#2b8cbe")


class LineRouteStop(Base):
    __tablename__ = "line_route_stops"

    id = Column(String, primary_key=True, default=_uuid)
    line_id = Column(String, ForeignKey("lines.id", ondelete="CASCADE"), index=True)
    stop_id = Column(String, ForeignKey("stops.id", ondelete="CASCADE"))
    idx = Column(Integer, default=0)


# --------------------------------------------------------------------------- #
# Demand model                                                                #
# --------------------------------------------------------------------------- #
class Mode(Base):
    __tablename__ = "modes"

    id = Column(String, primary_key=True, default=_uuid)
    scenario_id = Column(String, ForeignKey("scenarios.id", ondelete="CASCADE"), index=True)
    code = Column(String(8))   # PrT | PuT
    name = Column(String(64))
    is_prt = Column(Boolean, default=True)
    assignment = Column(String(16), default="uxsim")  # uxsim | none


class Activity(Base):
    """Trip purpose end (Home / Work / Other)."""

    __tablename__ = "activities"

    id = Column(String, primary_key=True, default=_uuid)
    scenario_id = Column(String, ForeignKey("scenarios.id", ondelete="CASCADE"), index=True)
    code = Column(String(8))   # H | W | O
    name = Column(String(64))
    is_home = Column(Boolean, default=False)


class DemandLayer(Base):
    """Activity pair (OD-DO layer), e.g. HW = Home->Work."""

    __tablename__ = "demand_layers"

    id = Column(String, primary_key=True, default=_uuid)
    scenario_id = Column(String, ForeignKey("scenarios.id", ondelete="CASCADE"), index=True)
    code = Column(String(8))   # HW | WH | HO | OH
    name = Column(String(64))
    from_activity = Column(String(8))   # production end (activity code)
    to_activity = Column(String(8))     # attraction end
    beta = Column(Float, default=0.1)   # gravity deterrence (per minute)
    # demand-strata generation (req #5): P_i = trip_rate * zone[prod_var]; A_j = zone[attr_var]
    prod_var = Column(String(16), default="population")    # population | workplaces
    attr_var = Column(String(16), default="workplaces")
    trip_rate = Column(Float, default=0.4)


class ZoneDemand(Base):
    """Per-zone, per-activity production & attraction."""

    __tablename__ = "zone_demand"

    id = Column(String, primary_key=True, default=_uuid)
    scenario_id = Column(String, ForeignKey("scenarios.id", ondelete="CASCADE"), index=True)
    zone_id = Column(String, ForeignKey("zones.id", ondelete="CASCADE"), index=True)
    activity = Column(String(8))  # activity code
    production = Column(Float, default=0.0)
    attraction = Column(Float, default=0.0)


class ModeChoiceParam(Base):
    """Binary-logit utility params per demand layer per mode."""

    __tablename__ = "mode_choice_params"

    id = Column(String, primary_key=True, default=_uuid)
    scenario_id = Column(String, ForeignKey("scenarios.id", ondelete="CASCADE"), index=True)
    demand_layer = Column(String(8))  # layer code
    mode_code = Column(String(8))     # PrT | PuT
    asc = Column(Float, default=0.0)        # alternative-specific constant
    beta_time = Column(Float, default=-0.05)  # utility per minute


# --------------------------------------------------------------------------- #
# Procedures (the function editor / 4-step sequence)                          #
# --------------------------------------------------------------------------- #
class Procedure(Base):
    __tablename__ = "procedures"

    id = Column(String, primary_key=True, default=_uuid)
    scenario_id = Column(String, ForeignKey("scenarios.id", ondelete="CASCADE"), index=True)
    idx = Column(Integer, default=0)            # execution order
    op_type = Column(String(32))                # see services/procedures.py OPS
    name = Column(String(128))
    params = Column(JSON, default=dict)
    active = Column(Boolean, default=True)
    last_status = Column(String(16), nullable=True)  # ok | error | skipped
    last_log = Column(Text, nullable=True)
