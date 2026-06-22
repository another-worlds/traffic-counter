"""Module 01 — seed & classes: class CRUD, the grid demo builder, FK validity, idempotency.

Like the contract tests these exercise REAL PostGIS (native geometry + FK cascade) and
skip cleanly when no database is reachable (see ``conftest.py``).
"""
from __future__ import annotations


def _new_scenario(client) -> str:
    r = client.post("/scenarios", json={"name": "seed-test"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_link_type_crud(client):
    sid = _new_scenario(client)
    r = client.post(f"/scenarios/{sid}/link-types",
                    json={"name": "arterial", "rank": 2, "num_lanes": 2,
                          "capacity_vph": 1800, "free_speed_kmh": 60})
    assert r.status_code == 201, r.text
    tid = r.json()["id"]
    assert r.json()["allowed_modes"] == ["PrT", "PuT"]  # column default round-trips

    lst = client.get(f"/scenarios/{sid}/link-types").json()
    assert any(t["id"] == tid for t in lst)
    assert client.get(f"/scenarios/{sid}/link-types/{tid}").json()["name"] == "arterial"

    assert client.delete(f"/scenarios/{sid}/link-types/{tid}").status_code == 204
    assert client.get(f"/scenarios/{sid}/link-types/{tid}").status_code == 404


def test_node_and_zone_type_crud(client):
    sid = _new_scenario(client)
    n = client.post(f"/scenarios/{sid}/node-types", json={"name": "signal", "control": "signalized"})
    assert n.status_code == 201, n.text
    assert n.json()["control"] == "signalized"
    z = client.post(f"/scenarios/{sid}/zone-types", json={"name": "res", "category": "residential"})
    assert z.status_code == 201, z.text
    assert z.json()["category"] == "residential"

    # cross-scenario isolation: a type from scenario A is 404 under scenario B
    other = _new_scenario(client)
    assert client.get(f"/scenarios/{other}/node-types/{n.json()['id']}").status_code == 404


def test_seed_demo_builds_network(client):
    sid = _new_scenario(client)
    r = client.post(f"/scenarios/{sid}/seed-demo?preset=grid3x3")
    assert r.status_code == 201, r.text
    c = r.json()
    assert c["nodes"] == 9
    assert c["links"] == 24
    assert c["zones"] == 4
    assert c["connectors"] == 4
    assert c["link_types"] == 3
    assert c["node_types"] == 2
    assert c["zone_types"] == 3
    assert c["modes"] == 2
    assert c["activities"] == 3
    assert c["demand_layers"] == 4
    assert c["zone_demand"] == 12

    fc = client.get(f"/scenarios/{sid}/network").json()
    kinds = [f["properties"]["kind"] for f in fc["features"]]
    assert kinds.count("node") == 9
    assert kinds.count("link") == 24
    assert kinds.count("zone") == 4
    assert all(f["geometry"] and f["geometry"].get("type") for f in fc["features"])


def test_seed_demo_fk_validity_and_length(client):
    sid = _new_scenario(client)
    client.post(f"/scenarios/{sid}/seed-demo")

    links = client.get(f"/scenarios/{sid}/links").json()
    type_ids = {t["id"] for t in client.get(f"/scenarios/{sid}/link-types").json()}
    assert links and all(l["link_type_id"] in type_ids for l in links)
    assert all(200 < l["length_m"] < 400 for l in links)  # ~300 m edges

    nodes = client.get(f"/scenarios/{sid}/nodes").json()
    assert all(n["node_type_id"] for n in nodes)


def test_seed_demo_idempotent(client):
    sid = _new_scenario(client)
    assert client.post(f"/scenarios/{sid}/seed-demo").status_code == 201
    # second seed without reset -> 409
    assert client.post(f"/scenarios/{sid}/seed-demo").status_code == 409
    # reset=true rebuilds cleanly: identical counts, no duplication
    r2 = client.post(f"/scenarios/{sid}/seed-demo?reset=true")
    assert r2.status_code == 201, r2.text
    assert r2.json()["nodes"] == 9 and r2.json()["links"] == 24
    fc = client.get(f"/scenarios/{sid}/network").json()
    assert [f["properties"]["kind"] for f in fc["features"]].count("node") == 9  # not 18


def test_seed_demo_bad_preset(client):
    sid = _new_scenario(client)
    assert client.post(f"/scenarios/{sid}/seed-demo?preset=nope").status_code == 400
