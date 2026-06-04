"""Shared router helpers: artifact persistence and GeoJSON builders."""
from __future__ import annotations

import uuid
from typing import Dict, List, Optional

import pandas as pd

from .models import AssignmentResult, ODMatrix
from .services import calibration, pipeline
from .storage import storage


# --- persistence ---------------------------------------------------------- #
def store_od(db, scenario_id: str, T, step: str, name: str,
             kind: str = "demand", mode: str = None, demand_layer: str = None) -> ODMatrix:
    key = f"{scenario_id}/mtx_{step}_{uuid.uuid4().hex}.parquet"
    storage.save_matrix(key, T)
    m = ODMatrix(scenario_id=scenario_id, name=name, step=step, n_zones=len(T),
                 storage_ref=key, kind=kind, mode_id=mode, demand_layer_id=demand_layer)
    db.add(m)
    db.flush()
    return m


def store_assignment(db, scenario_id: str, od_matrix_id: Optional[str], sim: Dict[str, float],
                     links: List[dict], targets: List[dict]):
    key = f"{scenario_id}/assign_{uuid.uuid4().hex}.parquet"
    storage.save_df(key, pd.DataFrame([{"link_id": k, "sim_vph": v} for k, v in sim.items()]))
    metrics = pipeline.evaluate(sim, targets)
    res = AssignmentResult(
        scenario_id=scenario_id, od_matrix_id=od_matrix_id, storage_ref=key,
        mean_geh=metrics["mean_geh"], rmse=metrics["rmse"],
        total_vkt=pipeline.total_vkt(sim, links),
    )
    db.add(res)
    db.flush()
    return res, metrics


def latest_assignment(db, scenario_id: str) -> Optional[AssignmentResult]:
    return (
        db.query(AssignmentResult)
        .filter(AssignmentResult.scenario_id == scenario_id)
        .order_by(AssignmentResult.created_at.desc())
        .first()
    )


def latest_distributed_od(db, scenario_id: str) -> Optional[ODMatrix]:
    return (
        db.query(ODMatrix)
        .filter(ODMatrix.scenario_id == scenario_id)
        .order_by(ODMatrix.created_at.desc())
        .first()
    )


# --- GeoJSON builders ----------------------------------------------------- #
def network_fc(nodes: List[dict], links: List[dict]) -> dict:
    feats = []
    for n in nodes:
        feats.append({"type": "Feature", "geometry": n["geom"],
                      "properties": {"kind": "node", "id": n["id"], "name": n.get("name")}})
    for l in links:
        feats.append({"type": "Feature", "geometry": l["geom"], "properties": {
            "kind": "link", "id": l["id"], "name": l.get("name"),
            "from": l["from_node_id"], "to": l["to_node_id"],
            "length_m": l.get("length_m"), "lanes": l.get("lanes"),
            "free_flow_speed_ms": l.get("free_flow_speed_ms"),
        }})
    return {"type": "FeatureCollection", "features": feats}


def counters_fc(counters) -> dict:
    feats = []
    for c in counters:
        feats.append({"type": "Feature", "geometry": c.geom, "properties": {
            "id": c.id, "name": c.name, "snapped_link_id": c.snapped_link_id,
            "link_direction": c.link_direction, "observed_vph": c.observed_vph,
            "pcu_vph": c.pcu_vph, "source_video_id": c.source_video_id,
            "source_line_id": c.source_line_id,
        }})
    return {"type": "FeatureCollection", "features": feats}


def linkflows_fc(links: List[dict], sim: Dict[str, float], targets: List[dict]) -> dict:
    obs = {t["link_id"]: float(t["target_vph"]) for t in targets}
    feats = []
    for l in links:
        lid = l["id"]
        sim_v = float(sim.get(lid, 0.0))
        lanes = int(l.get("lanes") or 1)
        cap = float(l.get("capacity_vph") or (lanes * 1800))
        props = {"kind": "link", "id": lid, "name": l.get("name"), "sim_vph": sim_v,
                 "lanes": lanes, "capacity_vph": cap, "length_m": l.get("length_m"),
                 "vc": (sim_v / cap) if cap else None}
        if lid in obs:
            o = obs[lid]
            g = float(calibration.geh([sim_v], [o])[0])
            props.update({"obs_vph": o, "geh": g, "ratio": (sim_v / o) if o else None})
        feats.append({"type": "Feature", "geometry": l["geom"], "properties": props})
    return {"type": "FeatureCollection", "features": feats}
