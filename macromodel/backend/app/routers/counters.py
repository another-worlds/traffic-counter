from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import counter_client
from ..config import settings
from ..db import get_db
from ..helpers import counters_fc
from ..models import Counter, Observation
from ..schemas import CounterCreate
from ..services import georef, loader
from .scenarios import get_scenario_or_404

router = APIRouter(tags=["counters"])


def _abs_url(rel) -> str | None:
    """Turn an API-relative file path (/files/...) into a browser-reachable absolute URL."""
    if not rel:
        return None
    if str(rel).startswith("http"):
        return rel
    return f"{settings.counter_public_url.rstrip('/')}{rel}"


@router.get("/counter-sources")
def counter_sources():
    """Proxy the traffic-counter app: projects -> videos -> counting lines.

    Best-effort: if the counter API is unreachable, returns reachable=False so the UI
    can still operate offline (counters can be created with manual volumes)."""
    try:
        projects = counter_client.list_projects()
    except Exception as e:  # noqa: BLE001 — surface as soft failure
        return {"reachable": False, "error": str(e), "projects": []}

    out = []
    for p in projects:
        videos_out = []
        try:
            videos = counter_client.list_videos(p["id"])
        except Exception:
            videos = []
        for v in videos:
            try:
                lines = counter_client.list_lines(v["id"])
            except Exception:
                lines = []
            videos_out.append({
                "video_id": v["id"], "filename": v.get("filename"),
                "duration_s": v.get("duration_s"), "status": v.get("status"),
                "lines": [{"line_id": ln["id"], "name": ln.get("name")} for ln in lines],
            })
        out.append({"project_id": p["id"], "name": p.get("name"), "videos": videos_out})
    return {"reachable": True, "projects": out}


@router.post("/scenarios/{scenario_id}/counters")
def create_counter(scenario_id: str, body: CounterCreate, db: Session = Depends(get_db)):
    get_scenario_or_404(db, scenario_id)
    nodes, links = loader.load_network(db, scenario_id)
    direction = (body.link_direction or "AB").upper()

    snapped_id = None
    if links:
        snapped = georef.snap_to_link(body.lon, body.lat, links)
        snapped_id = snapped["id"] if snapped else None
        if snapped_id and direction == "BA":
            rev = loader.reverse_link_id(links, snapped_id)
            if rev:
                snapped_id = rev  # attach the volume to the matching directed link

    c = Counter(
        scenario_id=scenario_id, name=body.name,
        geom={"type": "Point", "coordinates": [body.lon, body.lat]},
        snapped_link_id=snapped_id, link_direction=direction,
        source_video_id=body.source_video_id, source_line_id=body.source_line_id,
        observed_vph=body.observed_vph, pcu_vph=(body.pcu_vph or body.observed_vph),
    )
    db.add(c)
    db.commit()
    return {"id": c.id, "snapped_link_id": snapped_id, "link_direction": direction, "pcu_vph": c.pcu_vph}


@router.post("/scenarios/{scenario_id}/counters/{counter_id}/pull-observations")
def pull_observations(scenario_id: str, counter_id: str, db: Session = Depends(get_db)):
    get_scenario_or_404(db, scenario_id)
    c = db.query(Counter).filter(
        Counter.id == counter_id, Counter.scenario_id == scenario_id).first()
    if not c:
        raise HTTPException(404, "counter not found")
    if not (c.source_video_id and c.source_line_id):
        raise HTTPException(400, "counter has no traffic-counter source line")

    try:
        video = counter_client.get_video(c.source_video_id)
        counts = counter_client.compute_counts(c.source_video_id, [c.source_line_id])
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"traffic-counter API error: {e}")

    hours = max((video.get("duration_s") or 3600) / 3600.0, 1e-6)
    targets = georef.counts_to_targets(counts, c.source_line_id, c.link_direction, hours)
    if not targets:
        raise HTTPException(404, "no counts returned for that line")

    c.observed_vph = targets["observed_vph"]
    c.pcu_vph = targets["pcu_vph"]
    c.hours = hours
    db.query(Observation).filter(Observation.counter_id == c.id).delete()
    for cls, cnt in targets["by_class"].items():
        db.add(Observation(counter_id=c.id, link_id=c.snapped_link_id, vehicle_class=cls,
                           direction=georef.direction_key(c.link_direction),
                           count_total=float(cnt), hours=hours, vph=float(cnt) / hours))
    db.commit()
    return {"observed_vph": c.observed_vph, "pcu_vph": c.pcu_vph, "hours": hours}


@router.get("/scenarios/{scenario_id}/counters/{counter_id}/video-info")
def counter_video_info(scenario_id: str, counter_id: str, db: Session = Depends(get_db)):
    """Everything the detector popup needs: a frame, counts, per-segment + overall status,
    and a deep link into the counter's Count & Export view. Best-effort: every sub-call is
    isolated so a partial counter outage still returns whatever could be fetched."""
    get_scenario_or_404(db, scenario_id)
    c = db.query(Counter).filter(
        Counter.id == counter_id, Counter.scenario_id == scenario_id).first()
    if not c:
        raise HTTPException(404, "counter not found")

    info = {
        "reachable": True, "counter_id": c.id, "name": c.name,
        "source_video_id": c.source_video_id, "source_line_id": c.source_line_id,
        "link_direction": c.link_direction, "snapped_link_id": c.snapped_link_id,
        "observed_vph": c.observed_vph, "pcu_vph": c.pcu_vph,
        "video": None, "images": {}, "segments": [], "counts": None, "track_stats": None,
        "open_video_url": None,
    }
    if not c.source_video_id:
        return {**info, "reachable": False, "error": "no source video assigned"}

    try:
        video = counter_client.get_video(c.source_video_id)
    except Exception as e:  # noqa: BLE001
        return {**info, "reachable": False, "error": str(e)}

    info["video"] = {k: video.get(k) for k in
                     ("status", "progress_pct", "duration_s", "num_tracks",
                      "total_segments", "filename", "width", "height")}
    analyzed = video.get("status") == "analyzed"

    images = {}
    try:
        frames = counter_client.get_frames(c.source_video_id)
        if frames:
            images["keyframe"] = _abs_url(frames[0].get("url"))
    except Exception:  # noqa: BLE001
        pass
    if not images.get("keyframe"):
        try:
            images["keyframe"] = _abs_url(counter_client.get_frame_url(c.source_video_id).get("url"))
        except Exception:  # noqa: BLE001
            pass
    for key, fn in (("trajectories", counter_client.get_trajectories_url),
                    ("heatmap", counter_client.get_heatmap_url)):
        try:
            images[key] = _abs_url(fn(c.source_video_id).get("url"))
        except Exception:  # noqa: BLE001
            pass
    info["images"] = {k: v for k, v in images.items() if v}

    try:
        segs = counter_client.get_segments(c.source_video_id)
        info["segments"] = [{"idx": s.get("segment_idx"), "status": s.get("status"),
                             "start_time_s": s.get("start_time_s"), "end_time_s": s.get("end_time_s"),
                             "num_tracks": s.get("num_tracks")} for s in segs]
    except Exception:  # noqa: BLE001
        pass

    if c.source_line_id and analyzed:
        try:
            counts = counter_client.compute_counts(c.source_video_id, [c.source_line_id])
            per = next((p for p in counts.get("per_line", [])
                        if p.get("line_id") == c.source_line_id), None)
            if per:
                info["counts"] = {"line_name": per.get("line_name"), "total": per.get("total"),
                                  "by_class": per.get("by_class", {}),
                                  "by_direction": per.get("by_direction", {})}
        except Exception:  # noqa: BLE001
            pass
    if info["counts"] is None and analyzed:
        try:
            info["track_stats"] = counter_client.get_track_stats(c.source_video_id)
        except Exception:  # noqa: BLE001
            pass

    project_id = video.get("project_id")
    if project_id:
        info["open_video_url"] = (f"{settings.traffic_counter_ui_url.rstrip('/')}"
                                  f"/Count_and_export?project_id={project_id}"
                                  f"&video_id={c.source_video_id}")
    return info


@router.get("/scenarios/{scenario_id}/counters")
def list_counters(scenario_id: str, db: Session = Depends(get_db)):
    get_scenario_or_404(db, scenario_id)
    counters = db.query(Counter).filter(Counter.scenario_id == scenario_id).order_by(Counter.name).all()
    return counters_fc(counters)
