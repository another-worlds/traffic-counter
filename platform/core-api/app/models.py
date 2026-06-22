"""Canonical data model for the rebuilt macromodel core.

Every spatial entity stores **native PostGIS geometry** (WGS84/SRID 4326) with an
auto-created **GiST** index (``spatial_index=True``) — this is the deliberate break from
the legacy GeoJSON-in-a-JSON-column representation, and it is what makes KNN snapping
(``geom <-> point``), ``ST_DWithin``, and in-DB ``ST_AsMVT`` tiling first-class.

Field names track the **GMNS** (General Modeling Network Specification) link/node
vocabulary where natural (``from_node_id``/``to_node_id``, ``length_m``, ``lanes``,
``free_speed_kmh``, ``capacity_vph``) so GMNS import/export and AequilibraE/Path4GMNS
interchange stay cheap. OD matrices are stored out-of-row as **OMX** (HDF5) artefacts
referenced by ``storage_ref`` (the format that bridges to/from commercial Visum/EMME).

Scope of this foundation commit: the schema + spatial CRUD + KNN are exercised by tests;
the demand/procedure tables are defined here (they are part of the canonical contract)
but their routers/engines land in later commits.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from geoalchemy2 import Geometry
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

SRID = 4326


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _point():
    return Geometry(geometry_type="POINT", srid=SRID, spatial_index=True)


def _linestring():
    return Geometry(geometry_type="LINESTRING", srid=SRID, spatial_index=True)


def _polygon():
    return Geometry(geometry_type="POLYGON", srid=SRID, spatial_index=True)


# --------------------------------------------------------------------------- #
# Scenario container                                                          #
# --------------------------------------------------------------------------- #
class Scenario(Base):
    __tablename__ = "scenarios"

    id = Column(String, primary_key=True, default=_uuid)
    name = Column(String(255), nullable=False)
    description = Column(Text, default="")
    bbox = Column(JSON, nullable=True)  # [south, west, north, east] metadata
    # Copy-on-write scenario versioning (per the tooling-landscape report): a child
    # scenario points at its parent. Branching logic lands with the scenario service.
    parent_id = Column(String, ForeignKey("scenarios.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)

    nodes = relationship("Node", back_populates="scenario", cascade="all, delete-orphan")
    links = relationship("Link", back_populates="scenario", cascade="all, delete-orphan")
    zones = relationship("Zone", back_populates="scenario", cascade="all, delete-orphan")
    counters = relationship("Counter", back_populates="scenario", cascade="all, delete-orphan")


# --------------------------------------------------------------------------- #
# Network — spatial core (native geometry)                                    #
# --------------------------------------------------------------------------- #
class Node(Base):
    __tablename__ = "nodes"

    id = Column(String, primary_key=True, default=_uuid)
    scenario_id = Column(String, ForeignKey("scenarios.id", ondelete="CASCADE"), index=True)
    name = Column(String(128))
    geom = Column(_point(), nullable=False)  # GMNS node geometry (x_coord/y_coord)
    osm_id = Column(String(64), nullable=True)
    node_type_id = Column(String, ForeignKey("node_types.id", ondelete="SET NULL"), nullable=True)

    scenario = relationship("Scenario", back_populates="nodes")


class Link(Base):
    """A directed road link (A->B). A two-way street is two Link rows (GMNS convention)."""

    __tablename__ = "links"

    id = Column(String, primary_key=True, default=_uuid)
    scenario_id = Column(String, ForeignKey("scenarios.id", ondelete="CASCADE"), index=True)
    name = Column(String(128))
    from_node_id = Column(String, ForeignKey("nodes.id", ondelete="CASCADE"))
    to_node_id = Column(String, ForeignKey("nodes.id", ondelete="CASCADE"))
    geom = Column(_linestring(), nullable=False)
    length_m = Column(Float, default=0.0)          # GMNS length (geodesic, metres)
    lanes = Column(Integer, default=1)
    free_speed_kmh = Column(Float, default=50.0)   # GMNS free_speed
    capacity_vph = Column(Float, nullable=True)    # GMNS capacity (per lane)
    oneway = Column(Boolean, default=True)
    allowed_modes = Column(JSON, nullable=True)    # e.g. ["PrT","PuT"]; null -> all
    osm_id = Column(String(64), nullable=True)
    link_type_id = Column(String, ForeignKey("link_types.id", ondelete="SET NULL"), nullable=True)

    scenario = relationship("Scenario", back_populates="links")


class Zone(Base):
    """Traffic Analysis Zone (TAZ): a boundary polygon + a connector centroid."""

    __tablename__ = "zones"

    id = Column(String, primary_key=True, default=_uuid)
    scenario_id = Column(String, ForeignKey("scenarios.id", ondelete="CASCADE"), index=True)
    name = Column(String(128))
    geom = Column(_polygon(), nullable=True)         # boundary (optional)
    centroid = Column(_point(), nullable=True)       # load point
    connector_node_id = Column(String, ForeignKey("nodes.id", ondelete="SET NULL"), nullable=True)
    # demand-strata variables that drive trip generation
    population = Column(Float, default=0.0)
    workplaces = Column(Float, default=0.0)
    zone_type_id = Column(String, ForeignKey("zone_types.id", ondelete="SET NULL"), nullable=True)

    scenario = relationship("Scenario", back_populates="zones")


class Connector(Base):
    """Zone <-> node access link (geometry derived from zone centroid + node)."""

    __tablename__ = "connectors"

    id = Column(String, primary_key=True, default=_uuid)
    scenario_id = Column(String, ForeignKey("scenarios.id", ondelete="CASCADE"), index=True)
    zone_id = Column(String, ForeignKey("zones.id", ondelete="CASCADE"))
    node_id = Column(String, ForeignKey("nodes.id", ondelete="CASCADE"))
    direction = Column(String(12), default="both")  # origin | destination | both
    t0_min = Column(Float, default=0.5)
    weight = Column(Float, default=1.0)


class Counter(Base):
    """A georeferenced traffic counter bridging a traffic-counter counting line to a link."""

    __tablename__ = "counters"

    id = Column(String, primary_key=True, default=_uuid)
    scenario_id = Column(String, ForeignKey("scenarios.id", ondelete="CASCADE"), index=True)
    name = Column(String(128))
    geom = Column(_point(), nullable=False)          # where the user dropped it
    snapped_link_id = Column(String, ForeignKey("links.id", ondelete="SET NULL"), nullable=True)
    link_direction = Column(String(2), default="AB")  # which link dir the line's +ve maps to
    # provenance in the traffic-counter app
    source_video_id = Column(String(64), nullable=True)
    source_line_id = Column(String(64), nullable=True)
    # measured target
    observed_vph = Column(Float, nullable=True)
    pcu_vph = Column(Float, nullable=True)
    hours = Column(Float, nullable=True)

    scenario = relationship("Scenario", back_populates="counters")


class Stop(Base):
    """PuT stop point."""

    __tablename__ = "stops"

    id = Column(String, primary_key=True, default=_uuid)
    scenario_id = Column(String, ForeignKey("scenarios.id", ondelete="CASCADE"), index=True)
    name = Column(String(128))
    geom = Column(_point(), nullable=False)
    node_id = Column(String, ForeignKey("nodes.id", ondelete="SET NULL"), nullable=True)


# --------------------------------------------------------------------------- #
# Classes (Visum-style typing of network objects)                             #
# --------------------------------------------------------------------------- #
class LinkType(Base):
    __tablename__ = "link_types"

    id = Column(String, primary_key=True, default=_uuid)
    scenario_id = Column(String, ForeignKey("scenarios.id", ondelete="CASCADE"), index=True)
    name = Column(String(128))
    rank = Column(Integer, default=3)               # 1=major … higher=minor
    num_lanes = Column(Integer, default=1)
    capacity_vph = Column(Float, default=1800.0)    # per lane
    free_speed_kmh = Column(Float, default=50.0)
    allowed_modes = Column(JSON, default=lambda: ["PrT", "PuT"])


class NodeType(Base):
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
# PuT lines                                                                   #
# --------------------------------------------------------------------------- #
class Line(Base):
    __tablename__ = "lines"

    id = Column(String, primary_key=True, default=_uuid)
    scenario_id = Column(String, ForeignKey("scenarios.id", ondelete="CASCADE"), index=True)
    name = Column(String(128))
    tsys = Column(String(32), default="Bus")
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


class Activity(Base):
    __tablename__ = "activities"

    id = Column(String, primary_key=True, default=_uuid)
    scenario_id = Column(String, ForeignKey("scenarios.id", ondelete="CASCADE"), index=True)
    code = Column(String(8))   # H | W | O
    name = Column(String(64))
    is_home = Column(Boolean, default=False)


class DemandLayer(Base):
    """Activity pair (e.g. HW = Home->Work) + gravity/generation parameters."""

    __tablename__ = "demand_layers"

    id = Column(String, primary_key=True, default=_uuid)
    scenario_id = Column(String, ForeignKey("scenarios.id", ondelete="CASCADE"), index=True)
    code = Column(String(8))   # HW | WH | HO | OH
    name = Column(String(64))
    from_activity = Column(String(8))
    to_activity = Column(String(8))
    beta = Column(Float, default=0.1)                       # gravity deterrence (/min)
    prod_var = Column(String(16), default="population")     # population | workplaces
    attr_var = Column(String(16), default="workplaces")
    trip_rate = Column(Float, default=0.4)


class ZoneDemand(Base):
    __tablename__ = "zone_demand"

    id = Column(String, primary_key=True, default=_uuid)
    scenario_id = Column(String, ForeignKey("scenarios.id", ondelete="CASCADE"), index=True)
    zone_id = Column(String, ForeignKey("zones.id", ondelete="CASCADE"), index=True)
    activity = Column(String(8))
    production = Column(Float, default=0.0)
    attraction = Column(Float, default=0.0)


class ModeChoiceParam(Base):
    __tablename__ = "mode_choice_params"

    id = Column(String, primary_key=True, default=_uuid)
    scenario_id = Column(String, ForeignKey("scenarios.id", ondelete="CASCADE"), index=True)
    demand_layer = Column(String(8))
    mode_code = Column(String(8))
    asc = Column(Float, default=0.0)
    beta_time = Column(Float, default=-0.05)


# --------------------------------------------------------------------------- #
# Matrices, results, procedures (OMX / out-of-row artefacts)                  #
# --------------------------------------------------------------------------- #
class Matrix(Base):
    """Demand or skim matrix. Values live out-of-row as an OMX (HDF5) artefact."""

    __tablename__ = "matrices"

    id = Column(String, primary_key=True, default=_uuid)
    scenario_id = Column(String, ForeignKey("scenarios.id", ondelete="CASCADE"), index=True)
    name = Column(String(128))
    kind = Column(String(16), default="demand")  # demand | skim
    step = Column(String(32))                     # seed | distributed | calibrated | skim | loaded
    n_zones = Column(Integer)
    mode_id = Column(String, nullable=True)
    demand_layer_id = Column(String, nullable=True)
    storage_ref = Column(String(256))             # OMX key in object storage
    created_at = Column(DateTime(timezone=True), default=_now)


class AssignmentResult(Base):
    __tablename__ = "assignment_results"

    id = Column(String, primary_key=True, default=_uuid)
    scenario_id = Column(String, ForeignKey("scenarios.id", ondelete="CASCADE"), index=True)
    matrix_id = Column(String, nullable=True)
    storage_ref = Column(String(256))             # per-link sim volumes
    mean_geh = Column(Float, nullable=True)
    rmse = Column(Float, nullable=True)
    total_vkt = Column(Float, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)


class CalibrationRun(Base):
    """An ODME run. ``method`` is engine-agnostic (select-link incidence by default)."""

    __tablename__ = "calibration_runs"

    id = Column(String, primary_key=True, default=_uuid)
    scenario_id = Column(String, ForeignKey("scenarios.id", ondelete="CASCADE"), index=True)
    method = Column(String(64), default="select_link_odme")
    iterations = Column(Integer)
    matrix_id = Column(String, nullable=True)
    mean_geh = Column(Float, nullable=True)
    pct_geh_lt5 = Column(Float, nullable=True)
    converged = Column(Boolean, default=False)
    history = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)


class Procedure(Base):
    """A step in the re-runnable calculation sequence (the Visum procedure list)."""

    __tablename__ = "procedures"

    id = Column(String, primary_key=True, default=_uuid)
    scenario_id = Column(String, ForeignKey("scenarios.id", ondelete="CASCADE"), index=True)
    idx = Column(Integer, default=0)
    op_type = Column(String(32))
    name = Column(String(128))
    params = Column(JSON, default=dict)
    active = Column(Boolean, default=True)
    last_status = Column(String(16), nullable=True)
    last_log = Column(Text, nullable=True)
