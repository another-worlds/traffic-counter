"""Integration test for the georeferencing bridge (req 4): place a counter on a link
and convert traffic-counter counts into a directional vph / PCU target — exercised
through the real FastAPI routes with a SQLite stand-in and a stubbed counter client.
"""
import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from app import counter_client  # noqa: E402
from app.main import app  # noqa: E402


def _stub_counter_api(monkeypatch):
    monkeypatch.setattr(counter_client, "list_projects", lambda: [{"id": "p1", "name": "Proj"}])
    monkeypatch.setattr(counter_client, "list_videos",
                        lambda pid: [{"id": "v1", "filename": "a.mp4", "duration_s": 7200, "status": "analyzed"}])
    monkeypatch.setattr(counter_client, "list_lines", lambda vid: [{"id": "l1", "name": "North"}])
    monkeypatch.setattr(counter_client, "get_video", lambda vid: {"id": "v1", "duration_s": 7200})
    monkeypatch.setattr(counter_client, "compute_counts", lambda vid, lids: {"per_line": [{
        "line_id": "l1", "total": 2400,
        "by_class": {"car": 1600, "truck": 400, "bus": 400},
        "by_direction": {"positive": 1400, "negative": 1000},
    }]})


def test_counter_sources_and_pull_observations(monkeypatch):
    _stub_counter_api(monkeypatch)
    with TestClient(app) as c:
        src = c.get("/counter-sources").json()
        assert src["reachable"] is True
        assert src["projects"][0]["videos"][0]["lines"][0]["line_id"] == "l1"

        sid = c.post("/scenarios", json={"name": "t"}).json()["id"]
        c.post(f"/scenarios/{sid}/network/load-sample", json={})
        net = c.get(f"/scenarios/{sid}/network").json()
        link = next(f for f in net["features"] if f["properties"]["kind"] == "link")
        (x0, y0), (x1, y1) = link["geometry"]["coordinates"]
        lon, lat = (x0 + x1) / 2.0, (y0 + y1) / 2.0

        cc = c.post(f"/scenarios/{sid}/counters", json={
            "name": "C1", "lat": lat, "lon": lon,
            "source_video_id": "v1", "source_line_id": "l1", "link_direction": "AB",
        }).json()
        assert cc["snapped_link_id"]  # snapped to a real link

        out = c.post(f"/scenarios/{sid}/counters/{cc['id']}/pull-observations").json()
        # 2 h video, positive direction = 1400 veh -> 700 vph
        assert abs(out["observed_vph"] - 700.0) < 1e-6
        # PCU factor = (1600*1 + 400*2 + 400*2.5)/2400 = 3400/2400
        assert abs(out["pcu_vph"] - 700.0 * (3400.0 / 2400.0)) < 1e-3
