"""Health classification for the traffic-counter pipeline.

Turns raw metrics (pipeline state + GPU/host telemetry) into three named cluster
statuses, each with a human description and recommendation, plus a rolled-up
`overall` status. Pure functions only (no I/O) so it is trivially unit-testable
and reusable by the logger, dashboard, and report.

Clusters (independent failure axes — is it fast / is the box busy / is it alive):
  * throughput  -- idle | optimal | degraded | slow
  * resources   -- idle | optimal | underutilized | saturated
  * liveness    -- idle | healthy | at_risk | stalled | error

`overall` is the worst-severity cluster. Thresholds live in DEFAULT_CFG (tune
per-GPU) rather than as magic numbers. Note `progress_pct` is a 0..1 fraction.
The stall threshold aligns to the system's 120s (DECODE_STALL_TIMEOUT_S /
stale_claim_threshold_seconds).
"""
from __future__ import annotations


SEVERITY = {
    "idle": 0,
    "optimal": 1, "healthy": 1,
    "underutilized": 2, "degraded": 2, "at_risk": 2,
    "slow": 3, "saturated": 3,
    "stalled": 4,
    "error": 5,
}

DEFAULT_CFG = {
    # throughput (speed_ratio = video-seconds processed per wall-clock second)
    "speed_optimal": 1.0,
    "speed_slow": 0.3,
    # resources
    "gpu_util_busy": 30.0,      # >= this while analyzing => engaged
    "gpu_util_high": 90.0,
    "mem_saturated_pct": 90.0,  # VRAM / host RAM / container mem
    # liveness
    "stall_flat_s": 120.0,      # flat progress >= this + GPU idle => stalled
    "at_risk_flat_s": 60.0,     # flat progress in [at_risk, stall) => at_risk
    "gpu_idle_pct": 5.0,        # GPU util < this counts as idle
}


def _mk(status: str, description: str, recommendation: str) -> dict:
    return {"status": status, "description": description, "recommendation": recommendation}


def _analyzing(videos: list) -> list:
    return [v for v in videos if v.get("status") == "analyzing"]


# --------------------------------------------------------------------------- throughput

def classify_throughput(videos: list, cfg: dict) -> dict:
    active = _analyzing(videos)
    if not active:
        return _mk("idle", "No video is being analyzed.",
                   "Queue a video, or check the watcher if videos are waiting.")
    speeds = [v["speed_ratio"] for v in active if v.get("speed_ratio")]
    if not speeds:
        return _mk("optimal", "Analysis just started; no speed measured yet.",
                   "None — wait for the first segment to complete.")
    speed = min(speeds)  # report on the slowest active video
    if speed < cfg["speed_slow"]:
        return _mk("slow",
                   f"Processing at {speed:.2f}× realtime — slower than realtime, backlog will grow.",
                   "Check GPU contention and BATCH_SIZE; reduce input resolution or worker count "
                   "if the GPU is shared. Correlate with the resources cluster.")
    if speed < cfg["speed_optimal"]:
        return _mk("degraded",
                   f"Processing at {speed:.2f}× realtime — below the 1× baseline.",
                   "Check whether the GPU is underutilized (CPU decode or IO bottleneck) "
                   "via the resources cluster.")
    return _mk("optimal", f"Processing at {speed:.2f}× realtime.",
               "None — throughput is healthy.")


# --------------------------------------------------------------------------- resources

def _mem_pct(used, total):
    if not total:
        return None
    return 100.0 * (used or 0) / total


def classify_resources(videos: list, telemetry: dict, cfg: dict) -> dict:
    gpu = (telemetry or {}).get("gpu")
    host = (telemetry or {}).get("host")
    containers = (telemetry or {}).get("containers") or []
    analyzing = bool(_analyzing(videos))

    # Saturation check first (most severe in this cluster).
    sat = []
    if gpu:
        vram = _mem_pct(gpu.get("mem_used_mb"), gpu.get("mem_total_mb"))
        if vram is not None and vram > cfg["mem_saturated_pct"]:
            sat.append(f"VRAM {vram:.0f}%")
    if host:
        ram = _mem_pct(host.get("used_mb"), host.get("total_mb"))
        if ram is not None and ram > cfg["mem_saturated_pct"]:
            sat.append(f"host RAM {ram:.0f}%")
    for c in containers:
        if (c.get("mem_pct") or 0) > cfg["mem_saturated_pct"]:
            sat.append(f"{c.get('name')} mem {c['mem_pct']:.0f}%")
    if sat:
        return _mk("saturated", "Memory near capacity: " + ", ".join(sat) + ".",
                   "OOM risk — reduce BATCH_SIZE or the number of worker replicas; "
                   "the api container has a 4 GB cap.")

    if not analyzing:
        return _mk("idle", "No analysis running; resources are not under load.",
                   "None.")

    if gpu is None:
        return _mk("optimal", "Analysis running; GPU telemetry unavailable.",
                   "Install/expose nvidia-smi to the monitor for GPU insight.")

    util = gpu.get("util_pct") or 0
    if util < cfg["gpu_util_busy"]:
        return _mk("underutilized",
                   f"GPU util {util:.0f}% while a video is analyzing.",
                   "Bottleneck is upstream of the GPU — CPU video decode or disk IO, "
                   "or a stall. Check the liveness cluster and CPU/IO.")
    return _mk("optimal", f"GPU util {util:.0f}% — engaged.",
               "None — resources are healthy.")


# --------------------------------------------------------------------------- liveness

def _vkey_video(v: dict):
    return (v.get("completed_segments"), v.get("current_segment_idx"),
            round(v.get("progress_pct") or 0.0, 4))


def _vkey_hist(st: dict):
    return (st.get("done"), st.get("seg"), round(st.get("progress") or 0.0, 4))


def _flat_seconds(vid: str, current_key, history: list, now_epoch: float):
    """How long `vid`'s progress key has been unchanged, from history (oldest→newest)."""
    if not history:
        return 0.0
    oldest_same = None
    for e in history:
        st = e.get("vids", {}).get(vid)
        if st is None:
            oldest_same = None
            continue
        if _vkey_hist(st) == current_key:
            if oldest_same is None:
                oldest_same = e.get("epoch")
        else:
            oldest_same = None
    if oldest_same is None:
        return 0.0
    return max(0.0, now_epoch - oldest_same)


def classify_liveness(videos: list, telemetry: dict, history: list, cfg: dict,
                      now_epoch: float | None = None) -> dict:
    errored = [v for v in videos if v.get("status") == "error"]
    if errored:
        names = ", ".join(v.get("filename", "?") for v in errored[:3])
        return _mk("error", f"Video(s) in error state: {names}.",
                   "Inspect error_message via the API or `docker compose logs worker`; "
                   "re-queue after fixing the cause.")

    active = _analyzing(videos)
    if not active:
        return _mk("idle", "Nothing is analyzing.", "None.")

    gpu = (telemetry or {}).get("gpu")
    gpu_idle = gpu is not None and (gpu.get("util_pct") or 0) < cfg["gpu_idle_pct"]

    worst_flat = 0.0
    if now_epoch is not None and history:
        for v in active:
            flat = _flat_seconds(v["video_id"], _vkey_video(v), history, now_epoch)
            worst_flat = max(worst_flat, flat)

    if worst_flat >= cfg["stall_flat_s"] and (gpu is None or gpu_idle):
        return _mk("stalled",
                   f"Progress flat for {worst_flat:.0f}s and the GPU is idle — "
                   "likely the sentinel-deadlock class.",
                   "Check `docker compose logs worker`; restart the worker if needed. "
                   "The reaper will error the claim at the 120s threshold.")
    if worst_flat >= cfg["at_risk_flat_s"]:
        return _mk("at_risk",
                   f"Progress has not advanced for {worst_flat:.0f}s.",
                   "Watch closely — if it crosses 120s with an idle GPU it is a stall.")
    return _mk("healthy", "Progress is advancing.", "None — pipeline is alive.")


# --------------------------------------------------------------------------- overall

def classify(videos: list, telemetry: dict | None = None, history: list | None = None,
             cfg: dict | None = None, now_epoch: float | None = None) -> dict:
    cfg = cfg or DEFAULT_CFG
    videos = videos or []
    clusters = {
        "throughput": classify_throughput(videos, cfg),
        "resources": classify_resources(videos, telemetry or {}, cfg),
        "liveness": classify_liveness(videos, telemetry or {}, history or [], cfg, now_epoch),
    }
    worst_name = max(clusters, key=lambda k: SEVERITY.get(clusters[k]["status"], 0))
    worst = clusters[worst_name]
    overall = _mk(worst["status"],
                  f"[{worst_name}] {worst['description']}",
                  worst["recommendation"])
    return {**clusters, "overall": overall}


def severity(status: str) -> int:
    return SEVERITY.get(status, 0)
