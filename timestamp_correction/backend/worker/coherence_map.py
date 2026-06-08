"""
Ideal-day 1-minute hour presence map.

Maps each video onto a predetermined wall-clock window (default 00:00–24:00),
buckets 1-minute OCR samples into ideal UTC hours, and reports per-hour
OSD parseability (how much mapped footage has a parsed timestamp).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from typing import Any, Dict, List, Optional, Tuple

from .gap_detector import GapInterval, _merge_overlapping, gap_stats
from .sync_map import _bins_from_samples
from .timestamp_reader import TimestampSample

_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$")

PRESENCE_MODEL = "ideal_day_hour_presence_1m"
LEGACY_COHERENCE_MODEL = "ideal_day_vs_detected_1m"


def _parse_time_of_day(value: str) -> int:
    """Parse HH:MM or HH:MM:SS to seconds since midnight."""
    m = _TIME_RE.match(str(value).strip())
    if not m:
        raise ValueError(f"invalid time of day: {value!r}")
    h, mi, s = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
    if not (0 <= h <= 24 and 0 <= mi < 60 and 0 <= s < 60):
        raise ValueError(f"invalid time of day: {value!r}")
    if h == 24 and (mi > 0 or s > 0):
        raise ValueError(f"invalid time of day: {value!r}")
    return h * 3600 + mi * 60 + s


def _epoch_for_date_time(d: date, tod_seconds: int) -> float:
    dt = datetime.combine(d, time(), tzinfo=timezone.utc)
    return dt.timestamp() + float(tod_seconds)


def _date_from_epoch(epoch: float) -> date:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).date()


def resolve_calendar_date(
    samples: List[TimestampSample],
    *,
    date_mode: str,
    fixed_date: Optional[str],
) -> date:
    if date_mode == "fixed":
        if not fixed_date:
            raise ValueError("IDEAL_DAY_DATE required when IDEAL_DAY_DATE_MODE=fixed")
        return date.fromisoformat(fixed_date)

    for s in samples:
        if s.present and s.wall_clock_epoch is not None:
            return _date_from_epoch(s.wall_clock_epoch)

    return datetime.now(timezone.utc).date()


@dataclass
class PresenceBin:
    bin_idx: int
    start_frame: int
    end_frame: int
    start_t_s: float
    end_t_s: float
    detected_epoch: Optional[float]
    ocr_text: str
    present: bool
    ideal_epoch: Optional[float] = None
    hour_key: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bin_idx": self.bin_idx,
            "start_frame": self.start_frame,
            "end_frame": self.end_frame,
            "start_t_s": round(self.start_t_s, 3),
            "end_t_s": round(self.end_t_s, 3),
            "detected_epoch": self.detected_epoch,
            "ideal_epoch": round(self.ideal_epoch, 3) if self.ideal_epoch is not None else None,
            "ocr_text": self.ocr_text,
            "present": self.present,
            "hour_key": self.hour_key,
        }


# Backward-compat alias for tests/imports
CoherenceBin = PresenceBin


def _first_present_anchor(bins: List[PresenceBin]) -> Tuple[float, float]:
    for b in bins:
        if b.present and b.detected_epoch is not None:
            return b.start_t_s, float(b.detected_epoch)
    return 0.0, 0.0


def _assign_ideal_hours(
    bins: List[PresenceBin],
    *,
    day_start_epoch: float,
    window_s: float,
    video_duration_s: float,
    map_mode: str = "realtime",
) -> None:
    if video_duration_s <= 0:
        return
    anchor_video_t, anchor_epoch = _first_present_anchor(bins)
    if anchor_epoch <= 0:
        anchor_epoch = day_start_epoch

    if map_mode == "stretch":
        scale = window_s / video_duration_s

        def ideal_at(t_s: float) -> float:
            return day_start_epoch + t_s * scale
    else:

        def ideal_at(t_s: float) -> float:
            return anchor_epoch + (t_s - anchor_video_t)

    for b in bins:
        ideal = ideal_at(b.start_t_s)
        b.ideal_epoch = ideal
        b.hour_key = int(ideal // 3600) * 3600


def _hour_slots(day_start_epoch: float, window_s: float) -> List[int]:
    n_hours = max(1, int(window_s // 3600))
    return [int(day_start_epoch + i * 3600) for i in range(n_hours)]


def build_hour_presence(
    bins: List[PresenceBin],
    *,
    day_start_epoch: float,
    window_s: float,
) -> List[Dict[str, Any]]:
    slots = _hour_slots(day_start_epoch, window_s)
    by_hour: Dict[int, List[PresenceBin]] = {h: [] for h in slots}
    for b in bins:
        if b.hour_key is not None and b.hour_key in by_hour:
            by_hour[b.hour_key].append(b)

    rows: List[Dict[str, Any]] = []
    for hour_epoch in slots:
        hour_bins = sorted(by_hour.get(hour_epoch, []), key=lambda x: x.start_t_s)
        minutes_sampled = len(hour_bins)
        minutes_present = sum(1 for b in hour_bins if b.present)
        minutes_unparsed = minutes_sampled - minutes_present
        dt = datetime.fromtimestamp(hour_epoch, tz=timezone.utc)
        rows.append({
            "hour_start_epoch": hour_epoch,
            "hour_label": dt.strftime("%Y-%m-%d %H:00 UTC"),
            "minutes_sampled": minutes_sampled,
            "minutes_present": minutes_present,
            "minutes_unparsed": minutes_unparsed,
            "coverage_percent": round(100.0 * minutes_sampled / 60.0, 1),
            "parsed_percent": round(
                100.0 * minutes_present / max(minutes_sampled, 1), 1,
            ),
            "parsed_of_ideal_hour_percent": round(100.0 * minutes_present / 60.0, 1),
        })
    return rows


def build_hour_coherence(
    bins: List[PresenceBin],
    *,
    day_start_epoch: float,
    window_s: float,
) -> List[Dict[str, Any]]:
    """Legacy alias — returns hour_presence rows with coherence fields for old consumers."""
    rows = build_hour_presence(bins, day_start_epoch=day_start_epoch, window_s=window_s)
    for row in rows:
        row["minutes_coherent"] = row["minutes_present"]
        row["coherence_of_hour_percent"] = row["parsed_of_ideal_hour_percent"]
        row["gaps"] = []
    return rows


def gaps_from_unparsed_bins(bins: List[PresenceBin]) -> List[GapInterval]:
    gaps: List[GapInterval] = []
    for b in bins:
        if b.present:
            continue
        gaps.append(GapInterval(
            start_frame=b.start_frame,
            end_frame=b.end_frame,
            start_t_s=b.start_t_s,
            end_t_s=b.end_t_s,
            reason="missing_osd",
        ))
    return _merge_overlapping(gaps)


def gaps_from_incoherent_bins(bins: List[PresenceBin]) -> List[GapInterval]:
    """Legacy name — now only unparsed minutes."""
    return gaps_from_unparsed_bins(bins)


def _wall_clock_buckets_from_present_bins(
    bins: List[PresenceBin],
    fps: float,
) -> List[Dict[str, Any]]:
    if fps <= 0:
        return []
    buckets: Dict[int, Dict[str, Any]] = {}
    for b in bins:
        if not b.present or b.ideal_epoch is None:
            continue
        hour_key = int(b.ideal_epoch // 3600) * 3600
        if hour_key not in buckets:
            dt = datetime.fromtimestamp(hour_key, tz=timezone.utc)
            buckets[hour_key] = {
                "hour_start_epoch": hour_key,
                "hour_label": dt.strftime("%Y-%m-%d %H:00 UTC"),
                "start_frame": b.start_frame,
                "end_frame": b.end_frame,
            }
        buckets[hour_key]["start_frame"] = min(buckets[hour_key]["start_frame"], b.start_frame)
        buckets[hour_key]["end_frame"] = max(buckets[hour_key]["end_frame"], b.end_frame)
    return [buckets[k] for k in sorted(buckets)]


def _bin_usable_for_wall_clock(b: Dict[str, Any], model: str) -> bool:
    if model == LEGACY_COHERENCE_MODEL:
        return bool(b.get("coherent"))
    return bool(b.get("present"))


def frame_to_wall_epoch(
    frame_idx: int,
    presence_doc: Dict[str, Any],
    fps: float,
) -> Optional[float]:
    """Map frame to wall clock using present bins (or coherent for legacy artifacts)."""
    if fps <= 0:
        return None
    t_s = frame_idx / fps
    ideal_day = presence_doc.get("ideal_day") or {}
    map_mode = str(ideal_day.get("map_mode") or "realtime")
    day_start = float(ideal_day.get("day_start_epoch") or 0)
    window_s = float(ideal_day.get("window_s") or 86400)
    video_duration_s = float(presence_doc.get("video_duration_s") or 0)
    model = str(presence_doc.get("model") or PRESENCE_MODEL)

    anchor_video_t, anchor_epoch = 0.0, day_start
    for b in presence_doc.get("bins", []):
        if b.get("present") and b.get("detected_epoch") is not None:
            anchor_video_t = float(b.get("start_t_s") or 0)
            anchor_epoch = float(b["detected_epoch"])
            break

    for b in presence_doc.get("bins", []):
        if not _bin_usable_for_wall_clock(b, model):
            continue
        if int(b["start_frame"]) <= frame_idx < int(b["end_frame"]):
            if map_mode == "stretch" and video_duration_s > 0:
                return day_start + t_s * (window_s / video_duration_s)
            return anchor_epoch + (t_s - anchor_video_t)

    return None


def build_hour_presence_map(
    samples: List[TimestampSample],
    fps: float,
    total_frames: int,
    *,
    ideal_day_start: str = "00:00:00",
    ideal_day_end: str = "24:00:00",
    date_mode: str = "first_osd",
    fixed_date: Optional[str] = None,
    bin_duration_s: float = 60.0,
    map_mode: str = "realtime",
    **_kwargs: Any,
) -> Dict[str, Any]:
    video_duration_s = total_frames / fps if fps > 0 else 0.0
    start_tod = _parse_time_of_day(ideal_day_start)
    end_tod = _parse_time_of_day(ideal_day_end)
    window_s = float(end_tod - start_tod)
    if window_s <= 0:
        window_s = 86400.0

    cal_date = resolve_calendar_date(
        samples, date_mode=date_mode, fixed_date=fixed_date,
    )
    day_start_epoch = _epoch_for_date_time(cal_date, start_tod)

    raw_bins = _bins_from_samples(samples, fps, total_frames, bin_duration_s)
    bins: List[PresenceBin] = [
        PresenceBin(
            bin_idx=b.bin_idx,
            start_frame=b.start_frame,
            end_frame=b.end_frame,
            start_t_s=b.start_t_s,
            end_t_s=b.end_t_s,
            detected_epoch=b.detected_epoch,
            ocr_text=b.ocr_text,
            present=b.present,
        )
        for b in raw_bins
    ]
    _assign_ideal_hours(
        bins,
        day_start_epoch=day_start_epoch,
        window_s=window_s,
        video_duration_s=video_duration_s,
        map_mode=map_mode,
    )

    hour_presence = build_hour_presence(
        bins, day_start_epoch=day_start_epoch, window_s=window_s,
    )
    gaps = gaps_from_unparsed_bins(bins)
    gap_stats_dict = gap_stats(gaps, total_frames, fps)
    parsed_bins = sum(1 for b in bins if b.present)
    hours_with_coverage = sum(1 for h in hour_presence if h["minutes_sampled"] > 0)
    wall_buckets = _wall_clock_buckets_from_present_bins(bins, fps)

    gap_dicts = [
        {
            "start_frame": g.start_frame,
            "end_frame": g.end_frame,
            "start_t_s": g.start_t_s,
            "end_t_s": g.end_t_s,
            "reason": g.reason,
        }
        for g in gaps
    ]

    return {
        "version": 1,
        "model": PRESENCE_MODEL,
        "ideal_day": {
            "date": cal_date.isoformat(),
            "start": ideal_day_start,
            "end": ideal_day_end,
            "day_start_epoch": round(day_start_epoch, 3),
            "window_s": window_s,
            "map_mode": map_mode,
        },
        "video_duration_s": round(video_duration_s, 3),
        "bins": [b.to_dict() for b in bins],
        "hour_presence": hour_presence,
        "hour_coherence": build_hour_coherence(
            bins, day_start_epoch=day_start_epoch, window_s=window_s,
        ),
        "gaps": gap_dicts,
        "wall_clock_buckets": wall_buckets,
        "stats": {
            **gap_stats_dict,
            "num_bins": len(bins),
            "parsed_bins": parsed_bins,
            "parsed_fraction": round(parsed_bins / max(len(bins), 1), 4),
            "hours_with_coverage": hours_with_coverage,
            "hour_presence_map_enabled": True,
            "coherence_map_enabled": True,
        },
    }


def build_coherence_map(
    samples: List[TimestampSample],
    fps: float,
    total_frames: int,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Deprecated alias — builds hour presence map."""
    return build_hour_presence_map(samples, fps, total_frames, **kwargs)