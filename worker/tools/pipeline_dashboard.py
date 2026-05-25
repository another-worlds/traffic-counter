#!/usr/bin/env python3
"""Live terminal dashboard for the traffic-counter analysis pipeline.

A `rich` TUI that auto-refreshes (~2s) and shows, in one screen:
  * API health + clock
  * GPU util / VRAM / temp / power and compute processes (nvidia-smi)
  * Host RAM and per-container CPU/mem (free -m / docker stats)
  * Per-video pipeline progress: bar, "Segment N of M", speed (×realtime), ETA,
    and a segment strip (🟩 done / 🟧 analyzing / ⬜ pending)
  * Optionally, a tail of recent events from a pipeline_logger.py JSONL file

Usage:
  python worker/tools/pipeline_dashboard.py [--api URL] [-i SECONDS]
         [--events FILE] [--no-telemetry]

Requires `rich` (pip install -r worker/tools/requirements-tools.txt).
Ctrl-C to exit. If the API is down the screen shows a red banner and keeps
retrying.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import time
from collections import deque

import _health
import _probe
from _probe import ApiUnavailable

try:
    from rich.console import Console, Group
    from rich.layout import Layout
    from rich.live import Live
    from rich.panel import Panel
    from rich.progress import BarColumn, Progress, TextColumn
    from rich.table import Table
    from rich.text import Text
except ImportError:
    raise SystemExit(
        "rich is required: pip install -r worker/tools/requirements-tools.txt"
    )


SEG_GLYPH = {"done": "🟩", "analyzing": "🟧", "error": "🟥"}
EVENT_STYLE = {
    "segment_done": "green", "video_analyzed": "bold green",
    "segment_error": "red", "video_error": "bold red", "api_unavailable": "red",
    "stall_suspected": "bold yellow", "segment_started": "cyan",
    "health_change": "bold magenta",
}
# Severity -> rich style for health statuses.
HEALTH_STYLE = {
    "idle": "dim", "optimal": "green", "healthy": "green",
    "underutilized": "yellow", "degraded": "yellow", "at_risk": "yellow",
    "slow": "red", "saturated": "red", "stalled": "bold red", "error": "bold red",
}


def _resolve_events(flag_value):
    """Auto-discover the logger's JSONL. Precedence: --events, $PIPELINE_EVENTS,
    ./logs/pipeline_events.jsonl, newest ./pipeline_events_*.jsonl."""
    if flag_value:
        return flag_value
    env = os.environ.get("PIPELINE_EVENTS")
    if env and os.path.exists(env):
        return env
    default = os.path.join("logs", "pipeline_events.jsonl")
    if os.path.exists(default):
        return default
    candidates = sorted(glob.glob("pipeline_events_*.jsonl"),
                        key=os.path.getmtime, reverse=True)
    return candidates[0] if candidates else None


def _parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--api", default=_probe.DEFAULT_API,
                   help=f"API base URL (default {_probe.DEFAULT_API})")
    p.add_argument("-i", "--interval", type=float, default=2.0,
                   help="seconds between refreshes (default 2)")
    p.add_argument("--events", default=None,
                   help="pipeline_logger.py JSONL to tail for events/health history. "
                        "If omitted, auto-discovers $PIPELINE_EVENTS, "
                        "./logs/pipeline_events.jsonl, or the newest "
                        "./pipeline_events_*.jsonl")
    p.add_argument("--no-telemetry", action="store_true",
                   help="skip nvidia-smi/docker/free sampling")
    return p.parse_args(argv)


def _fmt_eta(seconds) -> str:
    if seconds is None:
        return "—"
    seconds = int(seconds)
    h, m = seconds // 3600, (seconds % 3600) // 60
    if h:
        return f"{h}h {m:02d}m"
    return f"{m}m {seconds % 60:02d}s"


def _fmt_timestamp(ts) -> str:
    """Format a datetime timestamp as HH:MM:SS."""
    if ts is None:
        return "—"
    if isinstance(ts, str):
        return ts[11:19]  # ISO format: take HH:MM:SS
    return ts.strftime("%H:%M:%S")


def _bar(used, total, width=20) -> Text:
    if not total:
        return Text("n/a", style="dim")
    frac = max(0.0, min(1.0, (used or 0) / total))
    filled = int(round(frac * width))
    style = "green" if frac < 0.75 else ("yellow" if frac < 0.9 else "red")
    t = Text()
    t.append("█" * filled, style=style)
    t.append("░" * (width - filled), style="dim")
    t.append(f" {used:.0f}/{total:.0f}MB ({frac*100:.0f}%)")
    return t


def _gpu_panel(gpu, procs) -> Panel:
    if gpu is None:
        return Panel(Text("nvidia-smi unavailable", style="dim"), title="GPU",
                     border_style="dim")
    rows = []
    util = gpu.get("util_pct") or 0
    util_style = "green" if util > 5 else "yellow"
    rows.append(Text.assemble(("util  ", "bold"),
                              (f"{util:>3.0f}%", util_style),
                              f"   temp {gpu.get('temp_c') or '—'}°C   "
                              f"pwr {gpu.get('power_w') or '—'}/{gpu.get('power_limit_w') or '—'}W"))
    rows.append(Text.assemble(("vram  ", "bold"),
                              _bar(gpu.get("mem_used_mb"), gpu.get("mem_total_mb"))))
    if procs:
        for pr in procs[:4]:
            rows.append(Text(f"  pid {pr['pid']:<7} {pr['name'][:24]:<24} "
                             f"{pr['vram_mb'] or 0:.0f}MB", style="dim"))
    return Panel(Group(*rows), title="GPU", border_style="cyan")


def _host_panel(host, containers) -> Panel:
    rows = []
    if host:
        rows.append(Text.assemble(("ram   ", "bold"),
                                  _bar(host.get("used_mb"), host.get("total_mb"))))
    else:
        rows.append(Text("free -m unavailable", style="dim"))
    if containers:
        t = Table(box=None, pad_edge=False, expand=True)
        t.add_column("container", style="white", overflow="ellipsis")
        t.add_column("cpu%", justify="right")
        t.add_column("mem%", justify="right")
        t.add_column("mem", justify="right", style="dim")
        for c in containers:
            t.add_row(c["name"], f"{c['cpu_pct'] or 0:.0f}",
                      f"{c['mem_pct'] or 0:.0f}", c["mem_used"])
        rows.append(t)
    return Panel(Group(*rows), title="Host / Containers", border_style="cyan")


def _seg_strip(segs, width=40) -> Text:
    """Render a video's segments as colored glyphs, truncated to `width`."""
    t = Text()
    shown = segs[:width]
    for s in shown:
        t.append(SEG_GLYPH.get(s["status"], "⬜"))
    if len(segs) > width:
        t.append(f" +{len(segs) - width}")
    return t


def _pipeline_panel(videos, segs_by_video, stalled_ids) -> Panel:
    if not videos:
        return Panel(Text("no videos queued or analyzing", style="dim"),
                     title="Pipeline", border_style="green")
    blocks = []
    for v in videos:
        vid = v["video_id"]
        stalled = vid in stalled_ids
        name = Text(v.get("filename") or vid, style="bold")
        status = v.get("status")
        badge = "🟧 analyzing" if status == "analyzing" else "🟨 queued"
        if stalled:
            badge = "⚠ STALLED?"
        head = Text.assemble(name, "  ", (badge, "bold yellow" if stalled else "white"))
        blocks.append(head)

        pct = (v.get("progress_pct") or 0.0) * 100
        prog = Progress(BarColumn(bar_width=40),
                        TextColumn("{task.percentage:>3.0f}%"),
                        expand=False)
        prog.add_task("", total=100, completed=pct)
        blocks.append(prog)

        meta = v.get("worker_status_text") or ""
        spd = v.get("speed_ratio")
        spd_s = f"{spd:.1f}×" if spd else "—"
        status_line = f"  {meta}   speed {spd_s}   ETA {_fmt_eta(v.get('eta_seconds'))}"
        started = v.get("started_analyzing_at")
        if started:
            status_line += f"   started {_fmt_timestamp(started)}"
        blocks.append(Text(status_line, style="dim"))
        segs = segs_by_video.get(vid)
        if segs:
            blocks.append(Text("  ").append_text(_seg_strip(segs)))
        blocks.append(Text(""))
    return Panel(Group(*blocks), title="Pipeline", border_style="green")


def _events_panel(events: deque) -> Panel:
    if not events:
        return Panel(Text("(no events yet)", style="dim"), title="Recent events",
                     border_style="magenta")
    rows = []
    for ev in events:
        etype = ev.get("type", "?")
        style = EVENT_STYLE.get(etype, "white")
        ts = (ev.get("ts") or "")[11:19]
        extra = ""
        if "segment_idx" in ev:
            extra += f" seg{ev['segment_idx']}"
        if ev.get("num_tracks") is not None:
            extra += f" tracks={ev['num_tracks']}"
        if etype == "stall_suspected":
            extra += f" flat={ev.get('flat_for_s')}s"
        rows.append(Text(f"{ts} {etype:<16} {ev.get('filename','')}{extra}", style=style))
    return Panel(Group(*rows), title="Recent events", border_style="magenta")


def _stats_panel(stats: dict, videos: list) -> Panel:
    if not stats:
        return Panel(Text("gathering 5-min window…", style="dim"),
                     title="Rolling 5-min stats", border_style="yellow")
    rows = []
    ws = stats.get("window_s", 0)
    rows.append(Text(f"window {ws:.0f}s  ({stats.get('n', 0)} samples)", style="dim"))
    gpu = stats.get("avg_gpu")
    spd = stats.get("avg_speed")
    gpu_style = "green" if (gpu is not None and gpu > 5) else "yellow"
    rows.append(Text.assemble(
        ("GPU util  ", "bold"),
        (f"{gpu:.0f}%" if gpu is not None else "—", gpu_style),
        "   speed  ",
        (f"{spd:.1f}×" if spd is not None else "—", "white"),
    ))
    etas = stats.get("etas", {})
    for v in videos:
        if v.get("status") != "analyzing":
            continue
        vid = v["video_id"]
        api_eta = v.get("eta_seconds")
        recent_eta = etas.get(vid)
        rows.append(Text(f"{(v.get('filename') or '?')[:24]}", style="bold"))
        rows.append(Text(f"  API ETA    {_fmt_eta(api_eta)}", style="dim"))
        if recent_eta is not None:
            diff = (recent_eta or 0) - (api_eta or recent_eta or 0)
            suffix = ""
            if api_eta and abs(diff) > 120:
                suffix = f"  ({'+' if diff>0 else ''}{_fmt_eta(abs(int(diff)))} {'slower' if diff>0 else 'faster'})"
            style = "cyan" if abs(diff) <= 600 else ("yellow" if diff > 0 else "green")
            rows.append(Text(f"  recent ETA {_fmt_eta(recent_eta)}{suffix}", style=style))
        else:
            rows.append(Text("  recent ETA — (building window)", style="dim"))
    if not any(v.get("status") == "analyzing" for v in videos):
        rows.append(Text("  no active analysis", style="dim"))
    return Panel(Group(*rows), title="Rolling 5-min stats", border_style="yellow")


def _health_panel(health: dict) -> Panel:
    overall = health["overall"]["status"]
    border = HEALTH_STYLE.get(overall, "white")
    rows = [Text.assemble(("PIPELINE  ", "bold"),
                          (overall.upper(), HEALTH_STYLE.get(overall, "white")))]
    for cluster in ("throughput", "resources", "liveness"):
        c = health[cluster]
        st = c["status"]
        rows.append(Text.assemble(
            (f"{cluster:<11}", "bold"),
            (f"{st:<13}", HEALTH_STYLE.get(st, "white")),
        ))
        rows.append(Text(f"  {c['description']}", style="dim"))
        if st not in ("idle", "optimal", "healthy"):
            rows.append(Text(f"  → {c['recommendation']}", style=HEALTH_STYLE.get(st, "white")))
    return Panel(Group(*rows), title="Health", border_style=border)


def _read_tail(path, n=8) -> deque:
    events = deque(maxlen=n)
    try:
        with open(path) as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if rec.get("kind") == "event":
                    events.append(rec)
    except OSError:
        pass
    return events


class Dashboard:
    def __init__(self, args):
        self.api = args.api
        self.interval = args.interval
        self.events_file = _resolve_events(args.events)
        self.telemetry = not args.no_telemetry
        self.console = Console()
        self.stall_track: dict[str, dict] = {}
        self._history: deque = deque()  # (epoch, gpu_util, per-video state)

    def _header(self, healthy: bool) -> Panel:
        dot = Text("● API up", style="green") if healthy else Text("● API DOWN", style="bold red")
        line = Text.assemble("traffic-counter pipeline   ", dot,
                             f"   {_probe.now_iso()}   refresh {self.interval}s")
        return Panel(line, border_style="green" if healthy else "red")

    def _track_stalls(self, videos):
        """Lightweight client-side stall flag (no GPU dep) for visual emphasis."""
        stalled = set()
        seen = set()
        for v in videos:
            if v.get("status") != "analyzing":
                continue
            vid = v["video_id"]
            seen.add(vid)
            key = (v.get("completed_segments"), v.get("current_segment_idx"),
                   round(v.get("progress_pct") or 0.0, 4))
            st = self.stall_track.get(vid)
            if st is None or st["key"] != key:
                self.stall_track[vid] = {"key": key, "since": time.monotonic()}
            elif time.monotonic() - st["since"] > 180:
                stalled.add(vid)
        for vid in list(self.stall_track):
            if vid not in seen:
                del self.stall_track[vid]
        return stalled

    def _record_sample(self, videos: list, gpu: dict | None):
        entry = {
            "epoch": _probe.now_epoch(),
            "gpu_util": (gpu or {}).get("util_pct"),
            "vids": {
                v["video_id"]: {
                    "speed": v.get("speed_ratio"),
                    "done": v.get("completed_segments") or 0,
                    "total": v.get("total_segments") or 0,
                    "seg": v.get("current_segment_idx"),
                    "progress": v.get("progress_pct") or 0.0,
                }
                for v in videos if v.get("status") == "analyzing"
            },
        }
        self._history.append(entry)
        cutoff = entry["epoch"] - 600
        while self._history and self._history[0]["epoch"] < cutoff:
            self._history.popleft()

    def _rolling_stats(self, videos: list) -> dict:
        """5-min rolling averages of GPU util, speed, and per-video recent-pace ETA."""
        now = _probe.now_epoch()
        window = [e for e in self._history if e["epoch"] >= now - 300]
        if not window:
            return {}
        gpu_vals = [e["gpu_util"] for e in window if e["gpu_util"] is not None]
        speed_vals = [v["speed"] for e in window
                      for v in e["vids"].values() if v.get("speed")]
        result = {
            "n": len(window),
            "window_s": now - window[0]["epoch"],
            "avg_gpu": sum(gpu_vals) / len(gpu_vals) if gpu_vals else None,
            "avg_speed": sum(speed_vals) / len(speed_vals) if speed_vals else None,
            "etas": {},
        }
        for v in videos:
            if v.get("status") != "analyzing":
                continue
            vid = v["video_id"]
            remaining = (v.get("total_segments") or 0) - (v.get("completed_segments") or 0)
            if remaining <= 0:
                result["etas"][vid] = 0.0
                continue
            with_vid = [e for e in window if vid in e["vids"]]
            if len(with_vid) < 2:
                continue
            oldest, newest = with_vid[0], with_vid[-1]
            delta_done = newest["vids"][vid]["done"] - oldest["vids"][vid]["done"]
            delta_t = newest["epoch"] - oldest["epoch"]
            if delta_done > 0 and delta_t > 0:
                result["etas"][vid] = remaining / (delta_done / delta_t)
        return result

    def render(self) -> Layout:
        healthy = True
        videos, segs_by_video = [], {}
        try:
            videos = _probe.worker_status(self.api)
            for v in videos:
                if v["status"] == "analyzing" and v.get("total_segments"):
                    try:
                        segs_by_video[v["video_id"]] = _probe.video_segments(
                            self.api, v["video_id"])
                    except ApiUnavailable:
                        pass
        except ApiUnavailable:
            healthy = False

        tel = _probe.telemetry_sample(self.telemetry)
        gpu = tel.get("gpu") if tel else None

        # Sort: analyzing before queued, then by GPU time consumed (completed segs) desc.
        videos.sort(key=lambda v: (
            0 if v.get("status") == "analyzing" else 1,
            -(v.get("completed_segments") or 0),
            -(v.get("progress_pct") or 0.0),
        ))

        stalled = self._track_stalls(videos)
        self._record_sample(videos, gpu)
        health = _health.classify(videos, tel, list(self._history),
                                  now_epoch=_probe.now_epoch())
        stats = self._rolling_stats(videos)

        layout = Layout()
        layout.split_column(
            Layout(self._header(healthy), size=3, name="header"),
            Layout(name="body"),
        )
        layout["body"].split_row(Layout(name="left", ratio=2), Layout(name="right"))
        layout["left"].update(_pipeline_panel(videos, segs_by_video, stalled))

        right_items = [
            _health_panel(health),
            _gpu_panel(gpu, tel.get("gpu_procs", [])) if self.telemetry
            else Panel(Text("telemetry disabled", style="dim"), title="GPU"),
            _host_panel(tel.get("host"), tel.get("containers", [])) if self.telemetry
            else Panel(Text("telemetry disabled", style="dim"), title="Host"),
            _stats_panel(stats, videos),
        ]
        if self.events_file:
            right_items.append(_events_panel(_read_tail(self.events_file)))
        layout["right"].update(Group(*right_items))
        return layout

    def run(self):
        with Live(self.render(), console=self.console, screen=True,
                  refresh_per_second=4) as live:
            try:
                while True:
                    time.sleep(self.interval)
                    live.update(self.render())
            except KeyboardInterrupt:
                pass


def main(argv=None):
    Dashboard(_parse_args(argv)).run()


if __name__ == "__main__":
    main()
