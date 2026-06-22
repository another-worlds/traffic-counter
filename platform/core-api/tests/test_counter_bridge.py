"""Module 04 — counter->link bridge: snap a directional count to a directed link.

Real PostGIS (GiST KNN + geodesic bearing); skips cleanly without a database.
"""
from __future__ import annotations


def _new_scenario(client) -> str:
    r = client.post("/scenarios", json={"name": "counter-test"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _node(client, sid, lon, lat) -> str:
    r = client.post(f"/scenarios/{sid}/nodes",
                    json={"geometry": {"type": "Point", "coordinates": [lon, lat]}})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _link(client, sid, a, b) -> str:
    r = client.post(f"/scenarios/{sid}/links", json={"from_node_id": a, "to_node_id": b})
    assert r.status_code == 201, r.text
    return r.json()["id"]


# A -> B is a roughly due-EAST link (forward bearing ~90 deg); MID sits on the segment.
A = (69.240, 41.311)
B = (69.250, 41.311)
MID = {"lon": 69.245, "lat": 41.311}


def test_ingest_direction_single_link(client):
    sid = _new_scenario(client)
    na, nb = _node(client, sid, *A), _node(client, sid, *B)
    lk_ab = _link(client, sid, na, nb)

    # hint EAST (~90) agrees with A->B  -> "AB"
    r = client.post(f"/scenarios/{sid}/counters/ingest",
                    json={**MID, "observed_vph": 800, "direction_hint_deg": 90})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["snapped_link_id"] == lk_ab
    assert body["link_direction"] == "AB"
    assert body["distance_m"] < 50
    assert body["created"] is True

    # hint WEST (~270) opposes A->B  -> "BA"
    r2 = client.post(f"/scenarios/{sid}/counters/ingest",
                     json={**MID, "observed_vph": 600, "direction_hint_deg": 270})
    assert r2.status_code == 201, r2.text
    assert r2.json()["link_direction"] == "BA"


def test_ingest_twin_links_disambiguation(client):
    sid = _new_scenario(client)
    na, nb = _node(client, sid, *A), _node(client, sid, *B)
    lk_ab = _link(client, sid, na, nb)   # A->B (bearing ~90)
    lk_ba = _link(client, sid, nb, na)   # B->A (bearing ~270), coincident geometry

    # hint EAST -> binds to the A->B row, labelled AB
    east = client.post(f"/scenarios/{sid}/counters/ingest",
                       json={**MID, "direction_hint_deg": 90}).json()
    assert east["snapped_link_id"] == lk_ab
    assert east["link_direction"] == "AB"

    # hint WEST -> binds to the B->A row, also labelled AB (its travel sense matches)
    west = client.post(f"/scenarios/{sid}/counters/ingest",
                       json={**MID, "direction_hint_deg": 270}).json()
    assert west["snapped_link_id"] == lk_ba
    assert west["link_direction"] == "AB"


def test_ingest_idempotent_on_provenance(client):
    sid = _new_scenario(client)
    na, nb = _node(client, sid, *A), _node(client, sid, *B)
    _link(client, sid, na, nb)

    p = {**MID, "direction_hint_deg": 90, "source_video_id": "v1", "source_line_id": "l1"}
    first = client.post(f"/scenarios/{sid}/counters/ingest", json={**p, "observed_vph": 100}).json()
    assert first["created"] is True

    second = client.post(f"/scenarios/{sid}/counters/ingest", json={**p, "observed_vph": 250})
    assert second.status_code == 201, second.text
    second = second.json()
    assert second["created"] is False
    assert second["counter_id"] == first["counter_id"]

    counters = client.get(f"/scenarios/{sid}/counters").json()
    assert len(counters) == 1                 # updated in place, not duplicated
    assert counters[0]["observed_vph"] == 250


def test_ingest_too_far_returns_422(client):
    sid = _new_scenario(client)
    na, nb = _node(client, sid, *A), _node(client, sid, *B)
    _link(client, sid, na, nb)
    r = client.post(f"/scenarios/{sid}/counters/ingest",
                    json={"lon": 69.40, "lat": 41.40, "direction_hint_deg": 90})
    assert r.status_code == 422, r.text


def test_resnap_keeps_binding(client):
    sid = _new_scenario(client)
    na, nb = _node(client, sid, *A), _node(client, sid, *B)
    lk_ab = _link(client, sid, na, nb)
    ing = client.post(f"/scenarios/{sid}/counters/ingest",
                      json={**MID, "direction_hint_deg": 90,
                            "source_video_id": "v1", "source_line_id": "l1"}).json()
    cid = ing["counter_id"]

    r = client.post(f"/scenarios/{sid}/counters/{cid}/resnap", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["snapped_link_id"] == lk_ab
    assert body["link_direction"] == "AB"     # preserved when no fresh hint is given
