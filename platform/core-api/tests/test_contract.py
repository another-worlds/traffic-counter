"""Proves the data-contract foundation: native-geometry round-trip, geodesic length,
the GeoJSON /network export, GiST-assisted KNN snapping, and that a spatial index exists.
"""
from __future__ import annotations


def _new_scenario(client) -> str:
    r = client.post("/scenarios", json={"name": "contract-test"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_geometry_roundtrip_and_length(client):
    sid = _new_scenario(client)
    # Two nodes ~ 835 m apart at this latitude (0.01 deg lon near 41.31 N).
    a = client.post(f"/scenarios/{sid}/nodes",
                    json={"name": "A", "geometry": {"type": "Point", "coordinates": [69.240, 41.311]}})
    b = client.post(f"/scenarios/{sid}/nodes",
                    json={"name": "B", "geometry": {"type": "Point", "coordinates": [69.250, 41.311]}})
    assert a.status_code == 201 and b.status_code == 201, (a.text, b.text)
    # Geometry survives the GeoJSON -> native -> GeoJSON round-trip.
    assert a.json()["geometry"] == {"type": "Point", "coordinates": [69.240, 41.311]}

    # Link with derived straight-line geometry + server-side geodesic length.
    lk = client.post(f"/scenarios/{sid}/links",
                     json={"from_node_id": a.json()["id"], "to_node_id": b.json()["id"], "lanes": 2})
    assert lk.status_code == 201, lk.text
    body = lk.json()
    assert body["geometry"]["type"] == "LineString"
    assert 700 < body["length_m"] < 950, body["length_m"]  # ~835 m
    assert body["lanes"] == 2


def test_network_featurecollection(client):
    sid = _new_scenario(client)
    n1 = client.post(f"/scenarios/{sid}/nodes",
                     json={"geometry": {"type": "Point", "coordinates": [69.24, 41.31]}}).json()
    n2 = client.post(f"/scenarios/{sid}/nodes",
                     json={"geometry": {"type": "Point", "coordinates": [69.25, 41.31]}}).json()
    client.post(f"/scenarios/{sid}/links",
                json={"from_node_id": n1["id"], "to_node_id": n2["id"]})
    client.post(f"/scenarios/{sid}/zones",
                json={"name": "Z1", "population": 1000,
                      "geometry": {"type": "Polygon",
                                   "coordinates": [[[69.24, 41.31], [69.25, 41.31],
                                                    [69.25, 41.32], [69.24, 41.31]]]}})
    fc = client.get(f"/scenarios/{sid}/network").json()
    assert fc["type"] == "FeatureCollection"
    kinds = {f["properties"]["kind"] for f in fc["features"]}
    assert {"node", "link", "zone"} <= kinds
    # Every feature carries a real geometry.
    assert all(f["geometry"] and f["geometry"].get("type") for f in fc["features"])


def test_knn_snap(client):
    sid = _new_scenario(client)
    n1 = client.post(f"/scenarios/{sid}/nodes",
                     json={"geometry": {"type": "Point", "coordinates": [69.240, 41.311]}}).json()
    n2 = client.post(f"/scenarios/{sid}/nodes",
                     json={"geometry": {"type": "Point", "coordinates": [69.250, 41.311]}}).json()
    # A second, far-away link that must NOT be chosen.
    f1 = client.post(f"/scenarios/{sid}/nodes",
                     json={"geometry": {"type": "Point", "coordinates": [69.40, 41.40]}}).json()
    f2 = client.post(f"/scenarios/{sid}/nodes",
                     json={"geometry": {"type": "Point", "coordinates": [69.41, 41.40]}}).json()
    near = client.post(f"/scenarios/{sid}/links",
                       json={"from_node_id": n1["id"], "to_node_id": n2["id"]}).json()
    client.post(f"/scenarios/{sid}/links",
                json={"from_node_id": f1["id"], "to_node_id": f2["id"]})

    # Drop a point right next to the near link's midpoint.
    r = client.post(f"/scenarios/{sid}/snap", json={"lon": 69.245, "lat": 41.3115})
    assert r.status_code == 200, r.text
    snap = r.json()
    assert snap["link_id"] == near["id"]
    assert snap["distance_m"] < 200  # within ~tens of metres


def test_gist_spatial_index_exists(client):
    """The whole point of native geometry: a GiST index on links.geom."""
    from sqlalchemy import text

    from app.db import engine

    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT indexdef FROM pg_indexes "
            "WHERE tablename = 'links' AND indexdef ILIKE '%gist%'"
        )).fetchall()
    assert rows, "expected a GiST spatial index on links.geom"
