#!/usr/bin/env python3
"""Analyze a pipeline_logger.py JSONL file and print two reports.

  1. Video resource usage — entities sorted by GPU wall-clock consumed
  2. 5-min bucket table — avg GPU util + avg speed per 5-min window
  3. ETA from the last 5 min of data (based on observed segment pace)

Useful for post-run diagnosis or as a quick live check:
  python worker/tools/pipeline_report.py pipeline_events_<ts>.jsonl
  python worker/tools/pipeline_report.py pipeline_events_<ts>.jsonl --last 30

Requires `rich` for pretty output (falls back to plain text without it).
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone

try:
    from rich.console import Console
    from rich.table import Table
    from rich.text import Text
    _console = Console()
    HAS_RICH = True
except ImportError:
    HAS_RICH = False

WINDOW_S = 300  # "last 5 min"


def _print(*args, markup=True):
    if HAS_RICH:
        _console.print(*args)
    else:
        print(*[str(a) for a in args])


def _fmt_hm(s) -> str:
    if s is None:
        return "—"
    s = int(s)
    return f"{s // 3600}h {(s % 3600) // 60:02d}m"


def _fmt_eta(s) -> str:
    if s is None:
        return "—"
    s = int(s)
    h, m = s // 3600, (s % 3600) // 60
    return f"{h}h {m:02d}m" if h else f"{m}m {s % 60:02d}s"


def _parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("file", help="JSONL log file from pipeline_logger.py")
    p.add_argument("--last", type=int, default=None, metavar="MINUTES",
                   help="only analyze the last N minutes of the log (default: all)")
    return p.parse_args(argv)


def _load(path: str, last_minutes: int | None) -> list:
    records = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except ValueError:
                continue
    if last_minutes and records:
        cutoff = max((r.get("epoch") or 0) for r in records) - last_minutes * 60
        records = [r for r in records if (r.get("epoch") or 0) >= cutoff]
    return records


# --------------------------------------------------------------------------- Section 1

def _entity_table(records: list) -> list:
    """Aggregate per-video resource usage from segment_done events."""
    vids: dict[str, dict] = {}

    for r in records:
        kind = r.get("kind")
        vid = r.get("video_id")
        if not vid:
            continue
        if kind == "event":
            if vid not in vids:
                vids[vid] = {"filename": r.get("filename", "?"),
                             "wall_s": 0.0, "tracks": 0, "segs": 0, "speeds": [],
                             "started_at": None, "finished_at": None}
            etype = r.get("type")
            if etype == "segment_done":
                vids[vid]["segs"] += 1
                vids[vid]["wall_s"] += r.get("wall_clock_s") or 0
                vids[vid]["tracks"] += r.get("num_tracks") or 0
                # Track the last finished_at (from the last segment done)
                if r.get("completed_at"):
                    vids[vid]["finished_at"] = r.get("completed_at")
        elif kind == "sample":
            for v in r.get("videos", []):
                sv = v.get("video_id")
                spd = v.get("speed_ratio")
                if sv and sv in vids:
                    if spd:
                        vids[sv]["speeds"].append(spd)
                    # Capture started_at from the first time we see it
                    if not vids[sv]["started_at"] and v.get("started_analyzing_at"):
                        vids[sv]["started_at"] = v.get("started_analyzing_at")

    return sorted(vids.values(), key=lambda v: -v["wall_s"])


def _print_entity_table(entities: list):
    if not entities:
        _print("[dim]No segment_done events found — log may be too short.[/dim]")
        return
    if HAS_RICH:
        t = Table(title="Video Resource Usage  (most GPU-time first)",
                  title_style="bold", border_style="green")
        t.add_column("#", justify="right", style="dim", width=3)
        t.add_column("filename", overflow="ellipsis", max_width=32)
        t.add_column("segs", justify="right")
        t.add_column("wall-clock", justify="right")
        t.add_column("tracks", justify="right")
        t.add_column("avg speed", justify="right")
        t.add_column("started", justify="right", style="dim")
        t.add_column("finished", justify="right", style="dim")
        for i, v in enumerate(entities, 1):
            speeds = v.get("speeds", [])
            avg_spd = f"{sum(speeds)/len(speeds):.1f}×" if speeds else "—"
            started = v.get("started_at", "—")
            if started and isinstance(started, str):
                started = started[11:19]  # extract HH:MM:SS from ISO timestamp
            finished = v.get("finished_at", "—")
            if finished and isinstance(finished, str):
                finished = finished[11:19]
            style = "bold" if i == 1 else ""
            t.add_row(str(i), v["filename"], str(v["segs"]),
                      _fmt_hm(v["wall_s"]), str(v["tracks"]), avg_spd,
                      started, finished, style=style)
        _console.print(t)
    else:
        print(f"\n{'#':>3}  {'filename':<32}  {'segs':>4}  {'wall-clock':>10}"
              f"  {'tracks':>6}  avg speed  started     finished")
        print("-" * 95)
        for i, v in enumerate(entities, 1):
            speeds = v.get("speeds", [])
            avg_spd = f"{sum(speeds)/len(speeds):.1f}x" if speeds else "—"
            started = v.get("started_at", "—")
            if started and isinstance(started, str):
                started = started[11:19]
            finished = v.get("finished_at", "—")
            if finished and isinstance(finished, str):
                finished = finished[11:19]
            print(f"{i:>3}  {v['filename']:<32}  {v['segs']:>4}  {_fmt_hm(v['wall_s']):>10}"
                  f"  {v['tracks']:>6}  {avg_spd:<9}  {started:<8}  {finished}")


# --------------------------------------------------------------------------- Section 2

def _bucket_rows(records: list) -> list:
    """Group sample + event records into 5-min UTC buckets."""
    buckets: dict[int, dict] = defaultdict(
        lambda: {"gpu": [], "speed": [], "segs_done": 0})
    for r in records:
        epoch = r.get("epoch") or 0
        bucket = (epoch // WINDOW_S) * WINDOW_S
        if r.get("kind") == "sample":
            gpu = (r.get("gpu") or {}).get("util_pct")
            if gpu is not None:
                buckets[bucket]["gpu"].append(gpu)
            for v in r.get("videos", []):
                spd = v.get("speed_ratio")
                if spd:
                    buckets[bucket]["speed"].append(spd)
        elif r.get("kind") == "event" and r.get("type") == "segment_done":
            buckets[bucket]["segs_done"] += 1
    return sorted(buckets.items())


def _print_bucket_table(rows: list):
    if not rows:
        _print("[dim]No sample records found.[/dim]")
        return
    if HAS_RICH:
        t = Table(title="5-min Buckets", title_style="bold", border_style="cyan")
        t.add_column("bucket (UTC)", style="dim")
        t.add_column("GPU util", justify="right")
        t.add_column("avg speed", justify="right")
        t.add_column("segs done", justify="right")
        last_ts = rows[-1][0]
        for ts, bkt in rows:
            label = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%H:%M")
            label_end = datetime.fromtimestamp(ts + WINDOW_S, tz=timezone.utc).strftime("%H:%M")
            gpu_s = (f"{sum(bkt['gpu'])/len(bkt['gpu']):.0f}%"
                     if bkt["gpu"] else "—")
            spd_s = (f"{sum(bkt['speed'])/len(bkt['speed']):.1f}×"
                     if bkt["speed"] else "—")
            style = "bold yellow" if ts == last_ts else ""
            t.add_row(f"{label}–{label_end}", gpu_s, spd_s,
                      str(bkt["segs_done"]), style=style)
        _console.print(t)
    else:
        print(f"\n{'bucket (UTC)':<14}  {'GPU util':>8}  {'avg speed':>9}  segs done")
        print("-" * 50)
        for ts, bkt in rows:
            label = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%H:%M")
            label_end = datetime.fromtimestamp(ts + WINDOW_S, tz=timezone.utc).strftime("%H:%M")
            gpu_s = (f"{sum(bkt['gpu'])/len(bkt['gpu']):.0f}%"
                     if bkt["gpu"] else "—")
            spd_s = (f"{sum(bkt['speed'])/len(bkt['speed']):.1f}x"
                     if bkt["speed"] else "—")
            print(f"{label}–{label_end:<10}  {gpu_s:>8}  {spd_s:>9}  {bkt['segs_done']}")


# --------------------------------------------------------------------------- Section 3

def _recent_eta(records: list) -> dict | None:
    """ETA based on the last WINDOW_S seconds of observed segment completions."""
    if not records:
        return None
    end_epoch = max((r.get("epoch") or 0) for r in records)
    start_epoch = end_epoch - WINDOW_S
    window = [r for r in records if (r.get("epoch") or 0) >= start_epoch]

    n_segs_done = sum(
        1 for r in window
        if r.get("kind") == "event" and r.get("type") == "segment_done"
    )
    gpu_vals = [(r.get("gpu") or {}).get("util_pct")
                for r in window if r.get("kind") == "sample"]
    gpu_vals = [v for v in gpu_vals if v is not None]
    speed_vals = [v.get("speed_ratio") for r in window if r.get("kind") == "sample"
                  for v in r.get("videos", []) if v.get("speed_ratio")]

    avg_gpu = sum(gpu_vals) / len(gpu_vals) if gpu_vals else None
    avg_speed = sum(speed_vals) / len(speed_vals) if speed_vals else None

    # Most recent pipeline state from the last sample record.
    last_sample = next((r for r in reversed(records) if r.get("kind") == "sample"), None)
    analyzing = [v for v in (last_sample or {}).get("videos", [])
                 if v.get("status") == "analyzing"]

    etas: dict[str, float] = {}
    pace_per_min: float | None = None
    if n_segs_done > 0:
        min_epoch = min((r.get("epoch") or end_epoch) for r in window if "epoch" in r)
        actual_window = max(end_epoch - min_epoch, 1)
        pace = n_segs_done / actual_window  # segs/sec
        pace_per_min = pace * 60
        for v in analyzing:
            remaining = (v.get("total_segments") or 0) - (v.get("completed_segments") or 0)
            if remaining > 0:
                etas[v.get("filename", "?")] = remaining / pace

    return {
        "window_s": end_epoch - start_epoch,
        "n_segs_done": n_segs_done,
        "avg_gpu": avg_gpu,
        "avg_speed": avg_speed,
        "pace_per_min": pace_per_min,
        "etas": etas,
        "analyzing": analyzing,
    }


def _print_recent_eta(info: dict | None):
    if not info:
        _print("[dim]No data for recent ETA.[/dim]")
        return

    _print("[bold]ETA from last 5-min window[/bold]" if HAS_RICH
           else "\n=== ETA from last 5-min window ===")

    avg_gpu = info["avg_gpu"]
    avg_spd = info["avg_speed"]
    pace = info["pace_per_min"]
    gpu_s = f"{avg_gpu:.0f}%" if avg_gpu is not None else "—"
    spd_s = f"{avg_spd:.1f}×" if avg_spd is not None else "—"
    pace_s = f"{pace:.2f} segs/min" if pace is not None else "—"
    _print(f"  window  {info['window_s']:.0f}s   "
           f"GPU util {gpu_s}   avg speed {spd_s}   pace {pace_s}")

    if info["etas"]:
        for fname, secs in info["etas"].items():
            api_eta = next((v.get("eta_seconds") for v in info["analyzing"]
                            if v.get("filename") == fname), None)
            api_s = f"  (API ETA {_fmt_eta(api_eta)})" if api_eta else ""
            _print(f"  {fname[:32]:<32}  recent ETA {_fmt_eta(secs)}{api_s}")
    elif info["analyzing"]:
        _print("  [dim]No segments completed in the last 5-min window — "
               "analysis may be starting or stalled.[/dim]" if HAS_RICH
               else "  No segments completed in last 5-min window.")
    else:
        _print("  [dim]No videos currently analyzing.[/dim]" if HAS_RICH
               else "  No videos currently analyzing.")


# --------------------------------------------------------------------------- Section 4

def _health_summary(records: list):
    """Time spent in each overall health status + the health_change timeline."""
    samples = [r for r in records if r.get("kind") == "sample" and r.get("health")]
    time_in: dict[str, float] = defaultdict(float)
    for a, b in zip(samples, samples[1:]):
        st = a["health"].get("overall", {}).get("status", "?")
        dt = (b.get("epoch") or 0) - (a.get("epoch") or 0)
        if 0 <= dt < 3600:  # ignore long gaps (logger restarts, rotation)
            time_in[st] += dt
    changes = [r for r in records
               if r.get("kind") == "event" and r.get("type") == "health_change"]
    return time_in, changes


def _print_health_summary(records: list):
    time_in, changes = _health_summary(records)
    if not time_in and not changes:
        return
    if HAS_RICH:
        from rich.table import Table as _T
        t = _T(title="Health: time-in-status", title_style="bold", border_style="magenta")
        t.add_column("overall status")
        t.add_column("time", justify="right")
        t.add_column("share", justify="right")
        total = sum(time_in.values()) or 1
        for st, secs in sorted(time_in.items(), key=lambda kv: -kv[1]):
            t.add_row(st, _fmt_eta(secs), f"{100*secs/total:.0f}%")
        _console.print(t)
    else:
        print("\nHealth: time-in-status")
        total = sum(time_in.values()) or 1
        for st, secs in sorted(time_in.items(), key=lambda kv: -kv[1]):
            print(f"  {st:<14} {_fmt_eta(secs):>10}  {100*secs/total:.0f}%")

    if changes:
        _print()
        _print("[bold]Health changes[/bold]" if HAS_RICH else "Health changes")
        for c in changes:
            ts = (c.get("ts") or "")[11:19]
            _print(f"  {ts}  {c.get('from_status')} → {c.get('to_status')}  "
                   f"[dim]{c.get('description','')}[/dim]" if HAS_RICH
                   else f"  {ts}  {c.get('from_status')} -> {c.get('to_status')}  "
                        f"{c.get('description','')}")


# --------------------------------------------------------------------------- main

def main(argv=None):
    args = _parse_args(argv)
    try:
        records = _load(args.file, args.last)
    except OSError as e:
        sys.exit(f"cannot open {args.file}: {e}")

    if not records:
        sys.exit("no records found in file")

    span = ""
    epochs = [r.get("epoch") for r in records if r.get("epoch")]
    if epochs:
        t0 = datetime.fromtimestamp(min(epochs), tz=timezone.utc).strftime("%H:%M:%S")
        t1 = datetime.fromtimestamp(max(epochs), tz=timezone.utc).strftime("%H:%M:%S")
        span = f"  [{t0} – {t1} UTC, {len(records)} records]"
    _print(f"[dim]{args.file}{span}[/dim]" if HAS_RICH else f"{args.file}{span}")
    _print()

    _print_entity_table(_entity_table(records))
    _print()
    _print_bucket_table(_bucket_rows(records))
    _print()
    _print_recent_eta(_recent_eta(records))
    _print()
    _print_health_summary(records)
    _print()


if __name__ == "__main__":
    main()
