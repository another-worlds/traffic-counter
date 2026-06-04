"""Cross-scenario isolation: routes that carry {scenario_id} in the path must reject
child resources (counters, OD matrices) that belong to a *different* scenario, instead
of silently reading/updating another scenario's data. Exercised through the real
FastAPI routes with a SQLite stand-in."""
import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


def _scenario_with_network(c):
    sid = c.post("/scenarios", json={"name": "s"}).json()["id"]
    c.post(f"/scenarios/{sid}/network/load-sample", json={})
    return sid


def _link_midpoint(c, sid):
    net = c.get(f"/scenarios/{sid}/network").json()
    link = next(f for f in net["features"] if f["properties"]["kind"] == "link")
    (x0, y0), (x1, y1) = link["geometry"]["coordinates"]
    return (x0 + x1) / 2.0, (y0 + y1) / 2.0


def test_counter_routes_reject_foreign_scenario():
    with TestClient(app) as c:
        a = _scenario_with_network(c)
        b = _scenario_with_network(c)
        lon, lat = _link_midpoint(c, b)
        cid = c.post(f"/scenarios/{b}/counters", json={
            "name": "c", "lon": lon, "lat": lat,
            "source_video_id": "v1", "source_line_id": "l1"}).json()["id"]

        # The counter lives in B; reaching it through A's URL must 404, not touch B's data.
        assert c.get(f"/scenarios/{a}/counters/{cid}/video-info").status_code == 404
        assert c.post(f"/scenarios/{a}/counters/{cid}/pull-observations").status_code == 404
        # Its own scenario still resolves it (the counter client is unreachable in tests,
        # so video-info soft-fails to reachable=False — but the route itself returns 200).
        assert c.get(f"/scenarios/{b}/counters/{cid}/video-info").status_code == 200


def test_assignment_rejects_od_matrix_from_other_scenario():
    with TestClient(app) as c:
        a = _scenario_with_network(c)
        b = _scenario_with_network(c)
        for sid in (a, b):
            c.post(f"/scenarios/{sid}/zones/auto", json={"n": 4})
        mid_b = c.post(f"/scenarios/{b}/trip-distribution", json={"beta": 0.1}).json()["od_matrix_id"]

        # A has its own zones (so it clears the zones gate), but B's matrix must be rejected
        # before any assignment runs.
        r = c.post(f"/scenarios/{a}/assignment", json={"od_matrix_id": mid_b})
        assert r.status_code == 404
