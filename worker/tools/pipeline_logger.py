#!/usr/bin/env python3
"""Structured JSONL logger for the traffic-counter analysis pipeline.

Polls the API (/worker/status + /videos/{id}/segments) and host telemetry
(nvidia-smi / docker stats / free -m) on an interval and writes one JSON object
per line. Two record kinds:

  * "event"  -- state transitions: video_queued, video_analyzing,
                segment_started, segment_done, segment_error, video_analyzed,
                video_error, stall_suspected
  * "sample" -- periodic snapshot of per-video progress + telemetry

The JSONL is machine-parseable (paste it back to Claude, or `jq` it) yet
skimmable. It catches the failure class we just fixed: a segment whose progress
goes flat while the worker still heartbeats and the GPU sits idle is flagged as
`stall_suspected` (default after 180s of no progress + GPU util < 5%).

Usage:
  python worker/tools/pipeline_logger.py [--api URL] [-i SECONDS]
         [-o FILE] [--stall-after SECONDS] [--no-telemetry]

Stop with Ctrl-C; the file is flushed after every line so a partial run is
still usable.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import deque
from datetime import datetime, timezone

import _health
import _probe
from _probe import ApiUnavailable


def _parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--api", default=_probe.DEFAULT_API,
                   help=f"API base URL (default {_probe.DEFAULT_API})")
    p.add_argument("-i", "--interval", type=float, default=5.0,
                   help="seconds between polls (default 5)")
    p.add_argument("-o", "--out", default=None,
                   help="output JSONL path (default pipeline_events_<ts>.jsonl)")
    p.add_argument("--stall-after", type=float, default=180.0,
                   help="flag a video as stalled after this many seconds of flat "
                        "progress while GPU util < 5%% (default 180)")
    p.add_argument("--no-telemetry", action="store_true",
                   help="skip nvidia-smi/docker/free sampling (pipeline events only)")
    p.add_argument("--max-bytes", type=int, default=50 * 1024 * 1024,
                   help="rotate the log to <file>.1 once it exceeds this size "
                        "(default 50MB; 0 disables rotation)")
    return p.parse_args(argv)


class PipelineLogger:
    def __init__(self, args):
        self.api = args.api
        self.interval = args.interval
        self.stall_after = args.stall_after
        self.telemetry = not args.no_telemetry
        self.max_bytes = args.max_bytes
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        self.out_path = args.out or f"pipeline_events_{ts}.jsonl"
        self.fh = open(self.out_path, "a", buffering=1)  # line-buffered
        self.bytes_written = self.fh.tell()
        # Per-video state carried across polls.
        self.videos: dict[str, dict] = {}
        # (video_id, segment_idx) -> last seen status, to detect transitions.
        self.seg_status: dict[tuple, str] = {}
        self.api_was_up = True
        # Telemetry/progress history for health trend (~last 10 min).
        self.history: deque = deque(maxlen=200)
        # Debounced overall-health tracking.
        self.confirmed_health: str | None = None
        self.health_candidate: dict | None = None

    # -- record emission ---------------------------------------------------

    def _write(self, rec: dict):
        rec.setdefault("ts", _probe.now_iso())
        rec.setdefault("epoch", _probe.now_epoch())
        line = json.dumps(rec, default=str) + "\n"
        self.fh.write(line)
        self.bytes_written += len(line.encode("utf-8"))
        if self.max_bytes and self.bytes_written >= self.max_bytes:
            self._rotate()

    def _rotate(self):
        """Roll the live log to <file>.1 (overwriting any previous .1) and start fresh."""
        self.fh.close()
        os.replace(self.out_path, self.out_path + ".1")
        self.fh = open(self.out_path, "a", buffering=1)
        self.bytes_written = 0
        print(f"[{_probe.now_iso()}] rotated log -> {self.out_path}.1", file=sys.stderr)

    def _event(self, etype: str, video: dict, **extra):
        rec = {
            "kind": "event",
            "type": etype,
            "video_id": video.get("video_id"),
            "filename": video.get("filename"),
        }
        rec.update(extra)
        self._write(rec)
        # Console line so the logger is useful in the foreground too.
        seg = f" seg={extra['segment_idx']}" if "segment_idx" in extra else ""
        print(f"[{rec['ts']}] {etype:<16} {video.get('filename','?')}{seg}",
              file=sys.stderr)

    # -- transition detection ---------------------------------------------

    def _diff_video(self, v: dict, segs: list):
        vid = v["video_id"]
        prev = self.videos.get(vid)
        status = v["status"]

        if prev is None:
            self._event("video_queued" if status == "queued" else "video_analyzing", v)
        elif prev["status"] != status:
            if status == "analyzing":
                self._event("video_analyzing", v)

        # Segment-level transitions (only analyzing videos carry segments).
        for s in segs:
            key = (vid, s["segment_idx"])
            old = self.seg_status.get(key)
            new = s["status"]
            if old == new:
                continue
            self.seg_status[key] = new
            if new == "analyzing":
                self._event("segment_started", v, segment_idx=s["segment_idx"],
                            start_time_s=s.get("start_time_s"),
                            end_time_s=s.get("end_time_s"),
                            started_at=s.get("started_at"))
            elif new == "done":
                self._event("segment_done", v, segment_idx=s["segment_idx"],
                            num_tracks=s.get("num_tracks"),
                            wall_clock_s=s.get("wall_clock_s"),
                            started_at=s.get("started_at"),
                            completed_at=s.get("completed_at"))
            elif new == "error":
                self._event("segment_error", v, segment_idx=s["segment_idx"],
                            error_message=s.get("error_message"))

        self._update_stall(v)

    def _update_stall(self, v: dict):
        """Track flat-progress episodes; emit one stall_suspected per episode."""
        vid = v["video_id"]
        progress_key = (v.get("completed_segments"), v.get("current_segment_idx"),
                        round(v.get("progress_pct") or 0.0, 4))
        st = self.videos.get(vid, {}).get("_stall")
        nowt = time.monotonic()
        if st is None or st["key"] != progress_key:
            st = {"key": progress_key, "since": nowt, "fired": False}
        v["_stall"] = st  # carried into self.videos by caller

    def _check_stalls(self, gpu: dict | None):
        """After updating all videos, fire stall events where progress is flat."""
        gpu_idle = gpu is not None and (gpu.get("util_pct") or 0) < 5
        # If we have no GPU reading at all, fall back to progress-only stall.
        for vid, v in self.videos.items():
            if v.get("status") != "analyzing":
                continue
            st = v.get("_stall")
            if not st or st["fired"]:
                continue
            flat_for = time.monotonic() - st["since"]
            if flat_for < self.stall_after:
                continue
            if gpu is None or gpu_idle:
                st["fired"] = True
                self._event("stall_suspected", v,
                            flat_for_s=round(flat_for, 1),
                            current_segment_idx=v.get("current_segment_idx"),
                            completed_segments=v.get("completed_segments"),
                            total_segments=v.get("total_segments"),
                            progress_pct=v.get("progress_pct"),
                            gpu_util_pct=None if gpu is None else gpu.get("util_pct"))

    def _detect_disappeared(self, seen_ids: set):
        """A video that drops off /worker/status finished or errored since last poll."""
        for vid in list(self.videos):
            if vid in seen_ids:
                continue
            prev = self.videos.pop(vid)
            # /worker/status only lists queued|analyzing, so a previously
            # analyzing video that vanished reached a terminal state.
            etype = "video_analyzed" if prev.get("status") == "analyzing" else "video_gone"
            self._event(etype, prev,
                         completed_segments=prev.get("completed_segments"),
                         total_segments=prev.get("total_segments"))

    # -- main loop ---------------------------------------------------------

    def poll_once(self):
        try:
            statuses = _probe.worker_status(self.api)
            if not self.api_was_up:
                self._write({"kind": "event", "type": "api_recovered"})
                self.api_was_up = True
        except ApiUnavailable as e:
            if self.api_was_up:
                self._write({"kind": "event", "type": "api_unavailable",
                             "error": str(e)})
                print(f"[{_probe.now_iso()}] api_unavailable {e}", file=sys.stderr)
                self.api_was_up = False
            return

        telemetry = _probe.telemetry_sample(self.telemetry)
        gpu = telemetry.get("gpu") if telemetry else None

        seen_ids = set()
        sample_videos = []
        for v in statuses:
            vid = v["video_id"]
            seen_ids.add(vid)
            segs = []
            if v["status"] == "analyzing" and v.get("total_segments"):
                try:
                    segs = _probe.video_segments(self.api, vid)
                except ApiUnavailable:
                    segs = []
            self._diff_video(v, segs)
            # Preserve carried stall state across the dict replacement.
            carried = v.pop("_stall", None)
            self.videos[vid] = v
            if carried is not None:
                self.videos[vid]["_stall"] = carried
            sample_videos.append({
                "video_id": vid,
                "filename": v.get("filename"),
                "status": v.get("status"),
                "progress_pct": v.get("progress_pct"),
                "current_segment_idx": v.get("current_segment_idx"),
                "completed_segments": v.get("completed_segments"),
                "total_segments": v.get("total_segments"),
                "speed_ratio": v.get("speed_ratio"),
                "eta_seconds": v.get("eta_seconds"),
                "started_analyzing_at": v.get("started_analyzing_at"),
                "analyzed_at": v.get("analyzed_at"),
            })

        self._detect_disappeared(seen_ids)
        self._check_stalls(gpu)
        self._record_history(sample_videos, gpu)

        health = _health.classify(sample_videos, telemetry, list(self.history),
                                  now_epoch=_probe.now_epoch())
        sample = {"kind": "sample", "videos": sample_videos, "health": health}
        if telemetry:
            sample.update({k: telemetry[k] for k in
                           ("gpu", "host", "containers") if k in telemetry})
        self._write(sample)
        self._emit_health_change(health)
        self._heartbeat(sample_videos, telemetry)

    def _record_history(self, videos: list, gpu: dict | None):
        self.history.append({
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
        })

    def _emit_health_change(self, health: dict):
        """Emit one health_change event only after a new overall status holds 2 polls."""
        new = health["overall"]["status"]
        if new == self.confirmed_health:
            self.health_candidate = None
            return
        if self.health_candidate and self.health_candidate["status"] == new:
            self.health_candidate["count"] += 1
        else:
            self.health_candidate = {"status": new, "count": 1}
        if self.health_candidate["count"] < 2:
            return
        prev = self.confirmed_health
        self.confirmed_health = new
        self.health_candidate = None
        self._write({
            "kind": "event", "type": "health_change",
            "from_status": prev, "to_status": new,
            "description": health["overall"]["description"],
            "recommendation": health["overall"]["recommendation"],
            "clusters": {k: health[k]["status"]
                         for k in ("throughput", "resources", "liveness")},
        })
        print(f"[{_probe.now_iso()}] health_change    {prev} -> {new} "
              f"({health['overall']['description']})", file=sys.stderr)

    def _heartbeat(self, videos: list, telemetry: dict):
        gpu = telemetry.get("gpu") if telemetry else None
        gpu_s = "gpu n/a"
        if gpu:
            gpu_s = (f"gpu {gpu.get('util_pct')}% "
                     f"{gpu.get('mem_used_mb')}/{gpu.get('mem_total_mb')}MB")
        active = [v for v in videos if v["status"] == "analyzing"]
        if active:
            v = active[0]
            vid_s = (f"{v['filename']} {((v.get('progress_pct') or 0)*100):.0f}% "
                     f"seg {v.get('completed_segments')}/{v.get('total_segments')}")
        else:
            vid_s = f"{len(videos)} queued/idle"
        print(f"[{_probe.now_iso()}] {vid_s} | {gpu_s} -> {self.out_path}",
              file=sys.stderr)

    def run(self):
        print(f"logging pipeline events to {self.out_path} (Ctrl-C to stop)",
              file=sys.stderr)
        self._write({"kind": "meta", "api": self.api, "interval": self.interval,
                     "stall_after": self.stall_after, "telemetry": self.telemetry})
        try:
            while True:
                self.poll_once()
                time.sleep(self.interval)
        except KeyboardInterrupt:
            print(f"\nstopped. log saved to: {self.out_path}", file=sys.stderr)
        finally:
            self.fh.close()


def main(argv=None):
    PipelineLogger(_parse_args(argv)).run()


if __name__ == "__main__":
    main()
