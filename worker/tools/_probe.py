"""Shared probes for the traffic-counter pipeline logger and dashboard.

Fuses two data sources both tools need:
  * analysis-pipeline state from the API (/worker/status, /videos/{id}/segments)
  * host/GPU telemetry from nvidia-smi / docker stats / free -m

Stdlib only (urllib + subprocess) so the tools have a single third-party dep
(`rich`, used by the dashboard alone). Telemetry probes degrade to None/[] when
nvidia-smi or docker are absent, so the tools still work on a plain host.

The nvidia-smi / docker stats query strings mirror worker/tools/monitor_run.sh
verbatim so both tools report the same numbers.
"""
from __future__ import annotations

import json
import subprocess
import urllib.error
import urllib.request
from datetime import datetime, timezone


DEFAULT_API = "http://localhost:8000"


class ApiUnavailable(Exception):
    """Raised when the API can't be reached or returns a non-2xx status."""


def now_iso() -> str:
    """UTC timestamp matching monitor_run.sh's `%Y-%m-%dT%H:%M:%SZ`."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def now_epoch() -> int:
    return int(datetime.now(timezone.utc).timestamp())


# --------------------------------------------------------------------------- API

def api_get(base: str, path: str, timeout: float = 5.0):
    url = base.rstrip("/") + path
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as e:
        raise ApiUnavailable(f"{url}: {e}") from e


def api_health(base: str, timeout: float = 5.0) -> bool:
    try:
        api_get(base, "/healthz", timeout=timeout)
        return True
    except ApiUnavailable:
        return False


def worker_status(base: str, timeout: float = 5.0) -> list:
    """List of queued/analyzing videos (see api/app/routers/worker.py)."""
    return api_get(base, "/worker/status", timeout=timeout)


def video_segments(base: str, video_id: str, timeout: float = 5.0) -> list:
    return api_get(base, f"/videos/{video_id}/segments", timeout=timeout)


# ---------------------------------------------------------------------- telemetry

def _run(cmd: list, timeout: float = 5.0) -> str | None:
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout


def _to_float(s: str):
    s = s.strip()
    if not s or s in ("[N/A]", "N/A", "[Not Supported]"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def gpu_sample() -> dict | None:
    """First GPU's util/VRAM/temp/power, or None when nvidia-smi is unavailable."""
    out = _run([
        "nvidia-smi",
        "--query-gpu=index,utilization.gpu,utilization.memory,memory.used,"
        "memory.total,temperature.gpu,power.draw,power.limit",
        "--format=csv,noheader,nounits",
    ])
    if not out:
        return None
    line = out.strip().splitlines()
    if not line:
        return None
    cols = [c.strip() for c in line[0].split(",")]
    if len(cols) < 8:
        return None
    return {
        "index": _to_float(cols[0]),
        "util_pct": _to_float(cols[1]),
        "mem_util_pct": _to_float(cols[2]),
        "mem_used_mb": _to_float(cols[3]),
        "mem_total_mb": _to_float(cols[4]),
        "temp_c": _to_float(cols[5]),
        "power_w": _to_float(cols[6]),
        "power_limit_w": _to_float(cols[7]),
    }


def gpu_procs() -> list:
    """Compute processes holding VRAM: [{pid, name, vram_mb}]."""
    out = _run([
        "nvidia-smi",
        "--query-compute-apps=pid,process_name,used_memory",
        "--format=csv,noheader,nounits",
    ])
    if not out:
        return []
    procs = []
    for line in out.strip().splitlines():
        cols = [c.strip() for c in line.split(",")]
        if len(cols) < 3:
            continue
        procs.append({
            "pid": cols[0],
            "name": cols[1],
            "vram_mb": _to_float(cols[2]),
        })
    return procs


def docker_stats() -> list:
    """Per-container [{name, cpu_pct, mem_used, mem_pct, pids}] or [] if unavailable."""
    out = _run([
        "docker", "stats", "--no-stream",
        "--format",
        "{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}\t{{.PIDs}}",
    ], timeout=8.0)
    if not out:
        return []
    rows = []
    for line in out.strip().splitlines():
        parts = line.split("\t")
        if len(parts) < 5:
            continue
        rows.append({
            "name": parts[0].strip(),
            "cpu_pct": _to_float(parts[1].replace("%", "")),
            "mem_used": parts[2].strip(),
            "mem_pct": _to_float(parts[3].replace("%", "")),
            "pids": parts[4].strip(),
        })
    return rows


def host_mem() -> dict | None:
    """Host RAM {used_mb, total_mb} parsed from `free -m` (monitor_run.sh line 115)."""
    out = _run(["free", "-m"])
    if not out:
        return None
    for line in out.splitlines():
        if line.startswith("Mem:"):
            cols = line.split()
            if len(cols) >= 3:
                total = _to_float(cols[1])
                used = _to_float(cols[2])
                return {"used_mb": used, "total_mb": total}
    return None


def telemetry_sample(include: bool = True) -> dict:
    """Combined host snapshot. Empty dict when telemetry is disabled."""
    if not include:
        return {}
    return {
        "gpu": gpu_sample(),
        "gpu_procs": gpu_procs(),
        "host": host_mem(),
        "containers": docker_stats(),
    }
