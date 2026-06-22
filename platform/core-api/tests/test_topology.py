"""Module 02 (slice 1) — topology split-link over real PostGIS; skips without a DB."""
from __future__ import annotations


def _new_scenario(client) -> str:
    r = client.post("/scenarios", json={"name": "topo-test"})
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


def _links(client, sid):
    return client.get(f"/scenarios/{sid}/links").json()


A = (69.240, 41.311)
B = (69.250, 41.311)   # ~835 m due-east of A


def test_split_preserves_length_and_connectivity(client):
    sid = _new_scenario(client)
    na, nb = _node(client, sid, *A), _node(client, sid, *B)
    lk = _link(client, sid, na, nb)
    before = _links(client, sid)
    assert len(before) == 1
    parent_len = before[0]["length_m"]

    r = client.post(f"/scenarios/{sid}/links/{lk}/split", json={"fraction": 0.5})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["removed_link_ids"] == [lk]
    assert len(body["link_ids"]) == 2
    assert body["new_node_id"]

    after = _links(client, sid)
    assert len(after) == 2                                  # parent replaced by two children
    assert lk not in {l["id"] for l in after}
    assert abs(sum(l["length_m"] for l in after) - parent_len) < 2.0   # length conserved

    by_id = {l["id"]: l for l in after}
    c1, c2 = by_id[body["link_ids"][0]], by_id[body["link_ids"][1]]
    assert c1["from_node_id"] == na and c1["to_node_id"] == body["new_node_id"]
    assert c2["from_node_id"] == body["new_node_id"] and c2["to_node_id"] == nb


def test_split_directed_twin(client):
    sid = _new_scenario(client)
    na, nb = _node(client, sid, *A), _node(client, sid, *B)
    ab = _link(client, sid, na, nb)
    _link(client, sid, nb, na)                              # the reverse twin
    r = client.post(f"/scenarios/{sid}/links/{ab}/split", json={"fraction": 0.5})
    assert r.status_code == 201, r.text
    body = r.json()
    assert len(body["removed_link_ids"]) == 2              # primary + twin
    assert len(body["link_ids"]) == 4
    assert len(_links(client, sid)) == 4


def test_split_resnaps_counter(client):
    sid = _new_scenario(client)
    na, nb = _node(client, sid, *A), _node(client, sid, *B)
    lk = _link(client, sid, na, nb)
    ing = client.post(f"/scenarios/{sid}/counters/ingest",
                      json={"lon": 69.2425, "lat": 41.311, "direction_hint_deg": 90,
                            "source_video_id": "v1", "source_line_id": "l1"}).json()
    cid = ing["counter_id"]
    assert ing["snapped_link_id"] == lk

    body = client.post(f"/scenarios/{sid}/links/{lk}/split", json={"fraction": 0.5}).json()
    a_child = body["link_ids"][0]                           # the A->mid child
    c = client.get(f"/scenarios/{sid}/counters/{cid}").json()
    assert c["snapped_link_id"] == a_child                  # re-snapped, not orphaned
    assert c["link_direction"] == "AB"                      # direction preserved


def test_split_at_endpoint_returns_400(client):
    sid = _new_scenario(client)
    na, nb = _node(client, sid, *A), _node(client, sid, *B)
    lk = _link(client, sid, na, nb)
    r = client.post(f"/scenarios/{sid}/links/{lk}/split", json={"fraction": 0.0})
    assert r.status_code == 400, r.text


def test_split_missing_link_404_and_no_target_422(client):
    sid = _new_scenario(client)
    na, nb = _node(client, sid, *A), _node(client, sid, *B)
    lk = _link(client, sid, na, nb)
    assert client.post(f"/scenarios/{sid}/links/does-not-exist/split",
                       json={"fraction": 0.5}).status_code == 404
    assert client.post(f"/scenarios/{sid}/links/{lk}/split", json={}).status_code == 422
