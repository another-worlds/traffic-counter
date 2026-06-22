"""Seed the Visum-style "base set" for a scenario: link/node/zone classes, modes,
activities, demand layers, mode-choice params, and a default 4-step procedure sequence.
All seeders are idempotent (guarded by existence) so they are safe to re-run.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from ..models import (
    Activity, DemandLayer, LinkType, Mode, ModeChoiceParam, NodeType, Procedure, ZoneType,
)

LINK_TYPES = [
    {"name": "Major", "rank": 1, "num_lanes": 2, "capacity_vph": 1800.0, "v0_kmh": 60.0},
    {"name": "Collector", "rank": 2, "num_lanes": 1, "capacity_vph": 1200.0, "v0_kmh": 50.0},
    {"name": "Local", "rank": 3, "num_lanes": 1, "capacity_vph": 800.0, "v0_kmh": 30.0},
]
NODE_TYPES = [
    {"name": "Uncontrolled", "control": "uncontrolled"},
    {"name": "Signalized", "control": "signalized"},
    {"name": "Roundabout", "control": "roundabout"},
    {"name": "Two-way stop", "control": "twoway_stop"},
]
ZONE_TYPES = [
    {"name": "Residential", "category": "residential"},
    {"name": "Employment", "category": "employment"},
    {"name": "Mixed", "category": "mixed"},
]
MODES = [
    {"code": "PrT", "name": "Private transport (car)", "is_prt": True, "assignment": "uxsim"},
    {"code": "PuT", "name": "Public transport", "is_prt": False, "assignment": "none"},
]
ACTIVITIES = [
    {"code": "H", "name": "Home", "is_home": True},
    {"code": "W", "name": "Work", "is_home": False},
    {"code": "O", "name": "Other", "is_home": False},
]
# Demand strata: each layer generates P_i = trip_rate * zone[prod_var], A_j = zone[attr_var]
DEMAND_LAYERS = [
    {"code": "HW", "name": "Home→Work", "from_activity": "H", "to_activity": "W", "beta": 0.10,
     "prod_var": "population", "attr_var": "workplaces", "trip_rate": 0.42},
    {"code": "WH", "name": "Work→Home", "from_activity": "W", "to_activity": "H", "beta": 0.10,
     "prod_var": "workplaces", "attr_var": "population", "trip_rate": 0.42},
    {"code": "HO", "name": "Home→Other", "from_activity": "H", "to_activity": "O", "beta": 0.14,
     "prod_var": "population", "attr_var": "population", "trip_rate": 0.30},
    {"code": "OH", "name": "Other→Home", "from_activity": "O", "to_activity": "H", "beta": 0.14,
     "prod_var": "population", "attr_var": "population", "trip_rate": 0.30},
]
# PrT is the reference (asc 0); PuT a bit less attractive but viable.
MODE_CHOICE = [
    {"mode_code": "PrT", "asc": 0.0, "beta_time": -0.06},
    {"mode_code": "PuT", "asc": -0.8, "beta_time": -0.04},
]
DEFAULT_PROCEDURES = [
    {"op_type": "TripGeneration", "name": "Trip generation", "params": {}},
    {"op_type": "TripDistribution", "name": "Trip distribution (gravity)", "params": {}},
    {"op_type": "ModeChoice", "name": "Mode choice (PrT/PuT logit)", "params": {}},
    {"op_type": "PrTAssignment", "name": "PrT assignment (UXsim)", "params": {}},
    {"op_type": "MatrixCorrection", "name": "Matrix correction (ODME vs detectors)", "params": {}},
    {"op_type": "Validation", "name": "Validation (GEH vs detectors)", "params": {}},
]


def seed_defaults(db: Session, scenario_id: str) -> None:
    def fresh(model):
        return db.query(model).filter(model.scenario_id == scenario_id).first() is None

    if fresh(LinkType):
        for r in LINK_TYPES:
            db.add(LinkType(scenario_id=scenario_id, allowed_modes=["PrT", "PuT"], **r))
    if fresh(NodeType):
        for r in NODE_TYPES:
            db.add(NodeType(scenario_id=scenario_id, **r))
    if fresh(ZoneType):
        for r in ZONE_TYPES:
            db.add(ZoneType(scenario_id=scenario_id, **r))
    if fresh(Mode):
        for r in MODES:
            db.add(Mode(scenario_id=scenario_id, **r))
    if fresh(Activity):
        for r in ACTIVITIES:
            db.add(Activity(scenario_id=scenario_id, **r))
    if fresh(DemandLayer):
        for r in DEMAND_LAYERS:
            db.add(DemandLayer(scenario_id=scenario_id, **r))
    if fresh(ModeChoiceParam):
        for layer in DEMAND_LAYERS:
            for mc in MODE_CHOICE:
                db.add(ModeChoiceParam(scenario_id=scenario_id, demand_layer=layer["code"], **mc))
    if fresh(Procedure):
        for i, p in enumerate(DEFAULT_PROCEDURES):
            db.add(Procedure(scenario_id=scenario_id, idx=i, active=True, **p))
    db.flush()
