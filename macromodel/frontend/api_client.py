"""httpx client to the macromodel-api (all sections)."""
from __future__ import annotations

import os
from typing import Dict, List, Optional

import httpx

BASE = os.environ.get("MACROMODEL_API_URL", "http://localhost:8100")


def _c(timeout: float = 300.0) -> httpx.Client:
    return httpx.Client(base_url=BASE, timeout=timeout)


def _j(r: httpx.Response):
    r.raise_for_status()
    return r.json()


# --- scenarios ---
def list_scenarios() -> List[Dict]:
    with _c(30) as c:
        return _j(c.get("/scenarios"))


def create_scenario(name: str) -> Dict:
    with _c() as c:
        return _j(c.post("/scenarios", json={"name": name}))


def create_demo() -> Dict:
    with _c() as c:
        return _j(c.post("/scenarios/demo"))


def load_sample(sid: str) -> Dict:
    with _c() as c:
        return _j(c.post(f"/scenarios/{sid}/network/load-sample", json={}))


def auto_zones(sid: str, n: int = 8) -> Dict:
    with _c() as c:
        return _j(c.post(f"/scenarios/{sid}/zones/auto", json={"n": n}))


# --- network map + geometric inserts ---
def get_map(sid: str) -> Dict:
    with _c() as c:
        return _j(c.get(f"/scenarios/{sid}/map"))


def link_flows(sid: str) -> Dict:
    with _c() as c:
        return _j(c.get(f"/scenarios/{sid}/results/link-flows"))


def insert_node(sid, lat, lon, name="node"):
    with _c() as c:
        return _j(c.post(f"/scenarios/{sid}/network/insert-node", json={"lat": lat, "lon": lon, "name": name}))


def insert_link(sid, from_node_id, to_node_id, link_type_id=None, oneway=False):
    with _c() as c:
        return _j(c.post(f"/scenarios/{sid}/network/insert-link",
                         json={"from_node_id": from_node_id, "to_node_id": to_node_id,
                               "link_type_id": link_type_id, "oneway": oneway}))


def insert_zone(sid, lat, lon, name="zone", polygon=None, population=0.0, workplaces=0.0):
    with _c() as c:
        return _j(c.post(f"/scenarios/{sid}/network/insert-zone",
                         json={"lat": lat, "lon": lon, "name": name, "polygon": polygon,
                               "population": population, "workplaces": workplaces}))


def select_in_bbox(sid, south, west, north, east):
    with _c() as c:
        return _j(c.post(f"/scenarios/{sid}/network/select-in-bbox",
                         json={"south": south, "west": west, "north": north, "east": east}))


def bulk_update(obj, ids, patch):
    with _c() as c:
        return _j(c.post(f"/objects/{obj}/bulk-update", json={"ids": ids, "patch": patch}))


def bulk_delete(obj, ids):
    with _c() as c:
        return _j(c.post(f"/objects/{obj}/bulk-delete", json={"ids": ids}))


def move_node(sid, node_id, lat, lon):
    with _c() as c:
        return _j(c.post(f"/scenarios/{sid}/network/move-node",
                         json={"node_id": node_id, "lat": lat, "lon": lon}))


def insert_stop(sid, lat, lon, name="stop"):
    with _c() as c:
        return _j(c.post(f"/scenarios/{sid}/network/insert-stop", json={"lat": lat, "lon": lon, "name": name}))


def insert_detector(sid, lat, lon, name="detector", link_direction="AB",
                    source_video_id=None, source_line_id=None):
    with _c() as c:
        return _j(c.post(f"/scenarios/{sid}/network/insert-detector",
                         json={"lat": lat, "lon": lon, "name": name, "link_direction": link_direction,
                               "source_video_id": source_video_id, "source_line_id": source_line_id}))


# --- generic objects (lists / class & demand editors) ---
def list_objects(sid: str, obj: str) -> List[Dict]:
    with _c() as c:
        return _j(c.get(f"/scenarios/{sid}/objects/{obj}"))


def create_object(sid: str, obj: str, body: Dict) -> Dict:
    with _c() as c:
        return _j(c.post(f"/scenarios/{sid}/objects/{obj}", json=body))


def update_object(obj: str, row_id: str, body: Dict) -> Dict:
    with _c() as c:
        return _j(c.patch(f"/objects/{obj}/{row_id}", json=body))


def delete_object(obj: str, row_id: str) -> None:
    with _c() as c:
        c.delete(f"/objects/{obj}/{row_id}")


# --- procedures ---
def list_procedures(sid: str) -> List[Dict]:
    with _c() as c:
        return _j(c.get(f"/scenarios/{sid}/procedures"))


def op_types() -> List[str]:
    with _c(30) as c:
        return _j(c.get("/procedures/op-types"))


def add_procedure(sid: str, op_type: str, name: str = None, params: Dict = None) -> Dict:
    with _c() as c:
        return _j(c.post(f"/scenarios/{sid}/procedures",
                         json={"op_type": op_type, "name": name or op_type, "params": params or {}}))


def update_procedure(pid: str, body: Dict) -> Dict:
    with _c() as c:
        return _j(c.patch(f"/procedures/{pid}", json=body))


def delete_procedure(pid: str) -> None:
    with _c() as c:
        c.delete(f"/procedures/{pid}")


def reorder_procedures(sid: str, ids: List[str]) -> Dict:
    with _c() as c:
        return _j(c.post(f"/scenarios/{sid}/procedures/reorder", json={"ids": ids}))


def run_procedures(sid: str) -> Dict:
    with _c() as c:
        return _j(c.post(f"/scenarios/{sid}/procedures/run"))


def run_one_procedure(pid: str) -> Dict:
    with _c() as c:
        return _j(c.post(f"/procedures/{pid}/run"))


# --- matrices ---
def list_matrices(sid: str) -> List[Dict]:
    with _c() as c:
        return _j(c.get(f"/scenarios/{sid}/matrices"))


def matrix_values(mid: str) -> Dict:
    with _c() as c:
        return _j(c.get(f"/matrices/{mid}/values"))


def update_cell(mid: str, i: int, j: int, value: float) -> Dict:
    with _c() as c:
        return _j(c.patch(f"/matrices/{mid}/cell", json={"i": i, "j": j, "value": value}))


def scale_matrix(mid: str, factor: float) -> Dict:
    with _c() as c:
        return _j(c.post(f"/matrices/{mid}/scale", json={"factor": factor}))


def blank_matrix(sid: str, name: str, fill: float = 0.0) -> Dict:
    with _c() as c:
        return _j(c.post(f"/scenarios/{sid}/matrices/blank", json={"name": name, "fill": fill}))


def delete_matrix(mid: str) -> None:
    with _c() as c:
        c.delete(f"/matrices/{mid}")


# --- counter sources (detector binding) ---
def counter_sources() -> Dict:
    with _c(30) as c:
        return _j(c.get("/counter-sources"))


def pull_observations(sid: str, cid: str) -> Dict:
    with _c() as c:
        return _j(c.post(f"/scenarios/{sid}/counters/{cid}/pull-observations"))
