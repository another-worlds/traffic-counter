"""
Build gap intervals from a timestamp timeline.

Gaps represent periods where footage is missing, frozen, or out of sync
with wall-clock time — common in stitched CCTV compilations.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from .timestamp_reader import TimestampSample


@dataclass
class GapInterval:
    start_frame: int
    end_frame: int
    start_t_s: float
    end_t_s: float
    reason: str


REASON_PRIORITY = {
    "missing_osd": 5,
    "time_reverse": 4,
    "time_jump": 3,
    "frozen_osd": 2,
    "sync_recovery": 1,
}


def primary_gap_reason(*reasons: str) -> str:
    """Pick the most severe reason when intervals overlap (legacy compound keys too)."""
    best = "sync_recovery"
    best_pri = -1
    for reason in reasons:
        for part in str(reason).split("+"):
            pri = REASON_PRIORITY.get(part, 0)
            if pri > best_pri:
                best_pri = pri
                best = part
    return best


def _expand_missing_run(
    samples: List[TimestampSample],
    start_i: int,
    end_i: int,
    fps: float,
) -> GapInterval:
    start = samples[start_i]
    end = samples[min(end_i, len(samples) - 1)]
    end_frame = end.frame_idx + max(1, int(fps * 5))
    return GapInterval(
        start_frame=start.frame_idx,
        end_frame=end_frame,
        start_t_s=start.t_seconds,
        end_t_s=end.t_seconds + 5.0,
        reason="missing_osd",
    )


def detect_gaps(
    samples: List[TimestampSample],
    fps: float,
    sample_interval_s: float,
    min_gap_samples: int = 3,
    jump_threshold_s: float = 15.0,
    frozen_threshold_s: float = 30.0,
) -> List[GapInterval]:
    if not samples:
        return []

    gaps: List[GapInterval] = []
    expected_step = sample_interval_s

    # 1. Missing OSD runs
    run_start: Optional[int] = None
    for i, s in enumerate(samples):
        if not s.present:
            if run_start is None:
                run_start = i
        else:
            if run_start is not None and (i - run_start) >= min_gap_samples:
                gaps.append(_expand_missing_run(samples, run_start, i - 1, fps))
            run_start = None
    if run_start is not None and (len(samples) - run_start) >= min_gap_samples:
        gaps.append(_expand_missing_run(samples, run_start, len(samples) - 1, fps))

    # 2. Time jumps, reversals, frozen OSD
    prev = samples[0]
    frozen_start: Optional[int] = None
    frozen_epoch: Optional[float] = None

    for i in range(1, len(samples)):
        cur = samples[i]
        if prev.present and cur.present:
            wall_delta = cur.wall_clock_epoch - prev.wall_clock_epoch
            frame_delta = cur.t_seconds - prev.t_seconds

            if wall_delta < -1.0:
                gaps.append(GapInterval(
                    start_frame=prev.frame_idx,
                    end_frame=cur.frame_idx,
                    start_t_s=prev.t_seconds,
                    end_t_s=cur.t_seconds,
                    reason="time_reverse",
                ))
            elif abs(wall_delta - frame_delta) > jump_threshold_s:
                gaps.append(GapInterval(
                    start_frame=prev.frame_idx,
                    end_frame=cur.frame_idx,
                    start_t_s=prev.t_seconds,
                    end_t_s=cur.t_seconds,
                    reason="time_jump",
                ))
            elif abs(wall_delta) < 0.5 and frame_delta > frozen_threshold_s:
                if frozen_start is None:
                    frozen_start = i - 1
                    frozen_epoch = prev.wall_clock_epoch
            else:
                if frozen_start is not None and frozen_epoch is not None:
                    gaps.append(GapInterval(
                        start_frame=samples[frozen_start].frame_idx,
                        end_frame=cur.frame_idx,
                        start_t_s=samples[frozen_start].t_seconds,
                        end_t_s=cur.t_seconds,
                        reason="frozen_osd",
                    ))
                frozen_start = None
                frozen_epoch = None

        prev = cur

    return _merge_overlapping(gaps)


def _merge_overlapping(gaps: List[GapInterval]) -> List[GapInterval]:
    if not gaps:
        return []
    sorted_gaps = sorted(gaps, key=lambda g: g.start_frame)
    merged = [sorted_gaps[0]]
    for g in sorted_gaps[1:]:
        last = merged[-1]
        if g.start_frame < last.end_frame:
            merged[-1] = GapInterval(
                start_frame=last.start_frame,
                end_frame=max(last.end_frame, g.end_frame),
                start_t_s=last.start_t_s,
                end_t_s=max(last.end_t_s, g.end_t_s),
                reason=primary_gap_reason(last.reason, g.reason),
            )
        elif g.start_frame == last.end_frame and last.reason == g.reason:
            merged[-1] = GapInterval(
                start_frame=last.start_frame,
                end_frame=max(last.end_frame, g.end_frame),
                start_t_s=last.start_t_s,
                end_t_s=max(last.end_t_s, g.end_t_s),
                reason=last.reason,
            )
        else:
            merged.append(g)
    return merged


def gap_stats(gaps: List[GapInterval], total_frames: int, fps: float) -> dict:
    gap_frames = sum(g.end_frame - g.start_frame for g in gaps)
    duration_s = total_frames / fps if fps > 0 else 0.0
    gap_duration_s = gap_frames / fps if fps > 0 else 0.0
    return {
        "num_gaps": len(gaps),
        "gap_frames": gap_frames,
        "gap_duration_s": round(gap_duration_s, 2),
        "total_frames": total_frames,
        "total_duration_s": round(duration_s, 2),
        "gap_fraction": round(gap_frames / total_frames, 4) if total_frames > 0 else 0.0,
        "by_reason": _count_by_reason(gaps),
    }


def _count_by_reason(gaps: List[GapInterval]) -> dict:
    counts: dict = {}
    for g in gaps:
        reason = primary_gap_reason(g.reason)
        counts[reason] = counts.get(reason, 0) + 1
    return counts


def normalize_by_reason(by_reason: dict) -> dict:
    """Collapse legacy compound reason keys from older scans."""
    counts: dict = {}
    for reason, count in (by_reason or {}).items():
        primary = primary_gap_reason(str(reason))
        counts[primary] = counts.get(primary, 0) + int(count)
    return counts