"""Thin httpx client to the macromodel-api (mirrors traffic-counter's frontend/api_client.py)."""
from __future__ import annotations

import os
from typing import Dict, List, Optional

import httpx

BASE = os.environ.get("MACROMODEL_API_URL", "http://localhost:8100")


def _c(timeout: float = 300.0) -> httpx.Client:
    return httpx.Client(base_url=BASE, timeout=timeout)


def _json(r: httpx.Response):
    r.raise_for_status()
    return r.json()


def create_demo() -> Dict:
    with _c() as c:
        return _json(c.post("/scenarios/demo"))


def create_scenario(name: str) -> Dict:
    with _c() as c:
        return _json(c.post("/scenarios", json={"name": name}))


def load_sample(scenario_id: str) -> Dict:
    with _c() as c:
        return _json(c.post(f"/scenarios/{scenario_id}/network/load-sample", json={}))


def auto_zones(scenario_id: str, n: int = 8) -> Dict:
    with _c() as c:
        return _json(c.post(f"/scenarios/{scenario_id}/zones/auto", json={"n": n}))


def get_network(scenario_id: str) -> Dict:
    with _c() as c:
        return _json(c.get(f"/scenarios/{scenario_id}/network"))


def get_zones(scenario_id: str) -> Dict:
    with _c() as c:
        return _json(c.get(f"/scenarios/{scenario_id}/zones"))


def get_counters(scenario_id: str) -> Dict:
    with _c() as c:
        return _json(c.get(f"/scenarios/{scenario_id}/counters"))


def counter_sources() -> Dict:
    with _c(timeout=30.0) as c:
        return _json(c.get("/counter-sources"))


def create_counter(scenario_id: str, name: str, lat: float, lon: float,
                   source_video_id: Optional[str] = None, source_line_id: Optional[str] = None,
                   link_direction: str = "AB", observed_vph: Optional[float] = None) -> Dict:
    with _c() as c:
        return _json(c.post(f"/scenarios/{scenario_id}/counters", json={
            "name": name, "lat": lat, "lon": lon,
            "source_video_id": source_video_id, "source_line_id": source_line_id,
            "link_direction": link_direction, "observed_vph": observed_vph,
        }))


def pull_observations(scenario_id: str, counter_id: str) -> Dict:
    with _c() as c:
        return _json(c.post(f"/scenarios/{scenario_id}/counters/{counter_id}/pull-observations"))


def run_4step(scenario_id: str, beta: float = 0.1) -> Dict:
    with _c() as c:
        return _json(c.post(f"/scenarios/{scenario_id}/run-4step", json={"beta": beta}))


def calibrate(scenario_id: str) -> Dict:
    with _c() as c:
        return _json(c.post(f"/scenarios/{scenario_id}/calibrate", json={}))


def link_flows(scenario_id: str) -> Dict:
    with _c() as c:
        return _json(c.get(f"/scenarios/{scenario_id}/results/link-flows"))


def summary(scenario_id: str) -> Dict:
    with _c() as c:
        return _json(c.get(f"/scenarios/{scenario_id}/results/summary"))
