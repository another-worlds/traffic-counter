"""Detector ↔ traffic-counter embedding: explicit link binding + the video-info
aggregator that drives the map popup. Exercised through the real FastAPI routes
with a SQLite stand-in and a stubbed counter client."""
import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from app import counter_client  # noqa: E402
from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402


def _sample_scenario(c):
    sid = c.post("/scenarios", json={"name": "t"}).json()["id"]
    c.post(f"/scenarios/{sid}/network/load-sample", json={})
    net = c.get(f"/scenarios/{sid}/network").json()
    link = next(f for f in net["features"] if f["properties"]["kind"] == "link")
    (x0, y0), (x1, y1) = link["geometry"]["coordinates"]
    node = next(f for f in net["features"] if f["properties"]["kind"] == "node")
    return sid, link["properties"]["id"], (x0 + x1) / 2.0, (y0 + y1) / 2.0, node["geometry"]["coordinates"]


def test_insert_detector_explicit_link_and_node_snap():
    with TestClient(app) as c:
        sid, link_id, lon, lat, _ = _sample_scenario(c)
        # Drag-to-assign binds the exact directed link the user picked, regardless of snapping.
        res = c.post(f"/scenarios/{sid}/network/insert-detector", json={
            "lat": lat, "lon": lon, "name": "North", "link_id": link_id, "snap": "node",
            "source_video_id": "v1", "source_line_id": "l1",
        }).json()
        assert res["snapped_link_id"] == link_id

        det = next(f for f in c.get(f"/scenarios/{sid}/map").json()["detectors"]["features"])
        # node-snap magnetizes the marker onto an existing node (not the raw drop point)
        node_xy = {tuple(n["geometry"]["coordinates"])
                   for n in c.get(f"/scenarios/{sid}/network").json()["features"]
                   if n["properties"]["kind"] == "node"}
        assert tuple(det["geometry"]["coordinates"]) in node_xy
        assert det["properties"]["source_video_id"] == "v1"
        assert det["properties"]["source_line_id"] == "l1"


def test_counter_video_info_aggregates_and_soft_fails(monkeypatch):
    monkeypatch.setattr(settings, "traffic_counter_public_url", "http://pub:8000")
    monkeypatch.setattr(settings, "traffic_counter_ui_url", "http://ui:8501")
    monkeypatch.setattr(counter_client, "get_video", lambda vid: {
        "id": vid, "project_id": "p1", "status": "analyzed", "duration_s": 3600,
        "num_tracks": 100, "total_segments": 1, "filename": "a.mp4"})
    monkeypatch.setattr(counter_client, "get_frames",
                        lambda vid: [{"index": 0, "time_s": 0, "url": "/files/p/v/frames/0.jpg"}])
    monkeypatch.setattr(counter_client, "get_trajectories_url",
                        lambda vid: {"url": "/files/p/v/trajectories.png"})
    monkeypatch.setattr(counter_client, "get_heatmap_url", lambda vid: {"url": "/files/p/v/heatmap.png"})
    monkeypatch.setattr(counter_client, "get_segments", lambda vid: [
        {"segment_idx": 0, "status": "done", "start_time_s": 0, "end_time_s": 3600, "num_tracks": 100}])
    monkeypatch.setattr(counter_client, "compute_counts", lambda vid, lids: {"per_line": [{
        "line_id": "l1", "line_name": "North", "total": 90,
        "by_class": {"car": 80, "truck": 10}, "by_direction": {"positive": 50, "negative": 40}}]})

    with TestClient(app) as c:
        sid, link_id, lon, lat, _ = _sample_scenario(c)
        cid = c.post(f"/scenarios/{sid}/network/insert-detector", json={
            "lat": lat, "lon": lon, "name": "North", "link_id": link_id,
            "source_video_id": "v1", "source_line_id": "l1"}).json()["id"]

        info = c.get(f"/scenarios/{sid}/counters/{cid}/video-info").json()
        assert info["reachable"] is True
        assert info["images"]["keyframe"] == "http://pub:8000/files/p/v/frames/0.jpg"
        assert info["images"]["trajectories"].startswith("http://pub:8000/")
        assert info["counts"]["total"] == 90
        assert info["counts"]["by_direction"]["positive"] == 50
        assert len(info["segments"]) == 1 and info["segments"][0]["status"] == "done"
        assert info["open_video_url"] == "http://ui:8501/Count_and_export?project_id=p1&video_id=v1"

        # A detector with no source video soft-fails rather than 500-ing.
        bare = c.post(f"/scenarios/{sid}/network/insert-detector",
                      json={"lat": lat, "lon": lon, "name": "bare"}).json()["id"]
        bad = c.get(f"/scenarios/{sid}/counters/{bare}/video-info").json()
        assert bad["reachable"] is False
