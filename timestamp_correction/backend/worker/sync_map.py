"""
Build a 1-minute ideal-vs-detected timestamp sync map for stitched CCTV.

Each video minute is a bin with:
  - detected wall clock (OCR)
  - ideal wall clock (linear from segment anchor)
  - residual = detected - ideal

After a stitch join (discontinuity), bins enter *recovery* until N consecutive
1-minute bins agree with each other (exclude-until-stable). Trusted bins form
segments used for frame→wall-clock mapping and hourly export buckets.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from .gap_detector import GapInterval, _merge_overlapping, gap_stats
from .timestamp_reader import TimestampSample

TRUST_TRUSTED = "trusted"
TRUST_RECOVERY = "recovery"
TRUST_UNSTABLE = "unstable"
TRUST_MISSING = "missing"


@dataclass
class MinuteBin:
    bin_idx: int
    start_frame: int
    end_frame: int
    start_t_s: float
    end_t_s: float
    detected_epoch: Optional[float]
    ocr_text: str
    present: bool
    ideal_epoch: Optional[float] = None
    residual_s: Optional[float] = None
    trust: str = TRUST_RECOVERY
    segment_id: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bin_idx": self.bin_idx,
            "start_frame": self.start_frame,
            "end_frame": self.end_frame,
            "start_t_s": round(self.start_t_s, 3),
            "end_t_s": round(self.end_t_s, 3),
            "detected_epoch": self.detected_epoch,
            "ideal_epoch": round(self.ideal_epoch, 3) if self.ideal_epoch is not None else None,
            "residual_s": round(self.residual_s, 3) if self.residual_s is not None else None,
            "ocr_text": self.ocr_text,
            "present": self.present,
            "trust": self.trust,
            "segment_id": self.segment_id,
        }


def _bins_from_samples(
    samples: List[TimestampSample],
    fps: float,
    total_frames: int,
    bin_duration_s: float,
) -> List[MinuteBin]:
    total_duration_s = total_frames / fps if fps > 0 else 0.0
    if total_duration_s <= 0:
        return []

    n_bins = max(1, int((total_duration_s + bin_duration_s - 1e-6) // bin_duration_s))
    if total_duration_s > n_bins * bin_duration_s:
        n_bins += 1

    stride_frames = max(1, int(round(fps * bin_duration_s)))
    bins: List[MinuteBin] = []
    sample_by_bin: Dict[int, TimestampSample] = {}
    for s in samples:
        bidx = int(s.t_seconds // bin_duration_s)
        prev = sample_by_bin.get(bidx)
        if prev is None or abs(s.t_seconds - (bidx + 0.5) * bin_duration_s) < abs(prev.t_seconds - (bidx + 0.5) * bin_duration_s):
            sample_by_bin[bidx] = s

    for i in range(n_bins):
        start_frame = i * stride_frames
        end_frame = min((i + 1) * stride_frames, total_frames)
        start_t_s = start_frame / fps if fps > 0 else 0.0
        end_t_s = end_frame / fps if fps > 0 else 0.0
        sample = sample_by_bin.get(i)
        if sample is not None:
            bins.append(MinuteBin(
                bin_idx=i,
                start_frame=start_frame,
                end_frame=end_frame,
                start_t_s=start_t_s,
                end_t_s=end_t_s,
                detected_epoch=sample.wall_clock_epoch,
                ocr_text=sample.ocr_text,
                present=sample.present,
            ))
        else:
            bins.append(MinuteBin(
                bin_idx=i,
                start_frame=start_frame,
                end_frame=end_frame,
                start_t_s=start_t_s,
                end_t_s=end_t_s,
                detected_epoch=None,
                ocr_text="",
                present=False,
            ))
    return bins


def _bins_coherent(a: MinuteBin, b: MinuteBin, tolerance_s: float) -> bool:
    if not a.present or not b.present:
        return False
    if a.detected_epoch is None or b.detected_epoch is None:
        return False
    wall_delta = b.detected_epoch - a.detected_epoch
    video_delta = b.start_t_s - a.start_t_s
    if wall_delta < -1.0:
        return False
    return abs(wall_delta - video_delta) <= tolerance_s


def _apply_exclude_until_stable(
    bins: List[MinuteBin],
    *,
    sync_tolerance_s: float,
    stable_bins_required: int,
) -> Tuple[List[Dict[str, Any]], int]:
    """State machine over bins. Returns segment metadata list."""
    segments: List[Dict[str, Any]] = []
    state = "recovery"
    stable_streak: List[MinuteBin] = []
    segment_id = -1
    anchor_frame = 0
    anchor_t_s = 0.0
    anchor_epoch = 0.0

    for i, b in enumerate(bins):
        if not b.present:
            b.trust = TRUST_MISSING
            state = "recovery"
            stable_streak = []
            continue

        if state == "recovery":
            if stable_streak and _bins_coherent(stable_streak[-1], b, sync_tolerance_s):
                stable_streak.append(b)
            else:
                stable_streak = [b]

            if len(stable_streak) < stable_bins_required:
                for sb in stable_streak:
                    sb.trust = TRUST_RECOVERY
                continue

            anchor_bin = stable_streak[0]
            segment_id += 1
            anchor_frame = anchor_bin.start_frame
            anchor_t_s = anchor_bin.start_t_s
            anchor_epoch = float(anchor_bin.detected_epoch or 0.0)
            state = "stable"
            for sb in stable_streak:
                sb.segment_id = segment_id
                sb.ideal_epoch = anchor_epoch + (sb.start_t_s - anchor_t_s)
                sb.residual_s = (sb.detected_epoch or 0.0) - sb.ideal_epoch
                sb.trust = TRUST_TRUSTED
            stable_streak = []
            continue

        # stable — compare to segment ideal line and previous trusted bin
        b.ideal_epoch = anchor_epoch + (b.start_t_s - anchor_t_s)
        b.residual_s = (b.detected_epoch or 0.0) - b.ideal_epoch
        b.segment_id = segment_id

        prev_trusted = next(
            (x for x in reversed(bins[:i]) if x.trust == TRUST_TRUSTED and x.segment_id == segment_id),
            None,
        )
        discontinuity = abs(b.residual_s) > sync_tolerance_s
        if prev_trusted is not None and not _bins_coherent(prev_trusted, b, sync_tolerance_s):
            discontinuity = True

        if discontinuity:
            b.trust = TRUST_UNSTABLE
            state = "recovery"
            stable_streak = []
        else:
            b.trust = TRUST_TRUSTED

    # Close segment spans from trusted bins
    by_segment: Dict[int, List[MinuteBin]] = {}
    for b in bins:
        if b.trust == TRUST_TRUSTED and b.segment_id is not None:
            by_segment.setdefault(b.segment_id, []).append(b)

    for sid in sorted(by_segment):
        seg_bins = by_segment[sid]
        first = seg_bins[0]
        anchor_epoch = float(first.detected_epoch or 0.0) - (first.residual_s or 0.0)
        segments.append({
            "segment_id": sid,
            "anchor_frame": first.start_frame,
            "anchor_t_s": first.start_t_s,
            "anchor_epoch": round(anchor_epoch, 3),
            "start_frame": first.start_frame,
            "end_frame": seg_bins[-1].end_frame,
            "start_t_s": first.start_t_s,
            "end_t_s": seg_bins[-1].end_t_s,
            "num_bins": len(seg_bins),
        })

    return segments, segment_id + 1


def _wall_clock_buckets(
    segments: List[Dict[str, Any]],
    fps: float,
) -> List[Dict[str, Any]]:
    """Hourly UTC buckets covering trusted segment spans (by mapped wall clock)."""
    if fps <= 0:
        return []

    buckets: Dict[int, Dict[str, Any]] = {}
    for seg in segments:
        anchor_frame = int(seg["anchor_frame"])
        anchor_epoch = float(seg["anchor_epoch"])
        anchor_t_s = float(seg["anchor_t_s"])
        start_f = int(seg["start_frame"])
        end_f = int(seg["end_frame"])

        hour_start = int(anchor_epoch // 3600) * 3600
        frame = start_f
        while frame < end_f:
            t_s = frame / fps
            epoch = anchor_epoch + (t_s - anchor_t_s)
            hour_key = int(epoch // 3600) * 3600
            if hour_key not in buckets:
                dt = datetime.fromtimestamp(hour_key, tz=timezone.utc)
                buckets[hour_key] = {
                    "hour_start_epoch": hour_key,
                    "hour_label": dt.strftime("%Y-%m-%d %H:00 UTC"),
                    "start_frame": frame,
                    "end_frame": frame,
                }
            buckets[hour_key]["end_frame"] = max(buckets[hour_key]["end_frame"], frame + 1)
            # advance ~1 minute of frames per step for bucket boundary detection
            frame += max(1, int(round(fps * 60)))

    return [buckets[k] for k in sorted(buckets)]


def gaps_from_bins(bins: List[MinuteBin]) -> List[GapInterval]:
    """Derive half-open frame gap intervals from non-trusted bins."""
    gaps: List[GapInterval] = []
    for b in bins:
        if b.trust == TRUST_TRUSTED:
            continue
        reason = {
            TRUST_MISSING: "missing_osd",
            TRUST_UNSTABLE: "time_jump",
            TRUST_RECOVERY: "sync_recovery",
        }.get(b.trust, "sync_recovery")
        gaps.append(GapInterval(
            start_frame=b.start_frame,
            end_frame=b.end_frame,
            start_t_s=b.start_t_s,
            end_t_s=b.end_t_s,
            reason=reason,
        ))
    return _merge_overlapping(gaps)


def build_sync_map(
    samples: List[TimestampSample],
    fps: float,
    total_frames: int,
    *,
    bin_duration_s: float = 60.0,
    sync_tolerance_s: float = 5.0,
    stable_bins_required: int = 3,
) -> Dict[str, Any]:
    bins = _bins_from_samples(samples, fps, total_frames, bin_duration_s)
    segments, num_segments = _apply_exclude_until_stable(
        bins,
        sync_tolerance_s=sync_tolerance_s,
        stable_bins_required=stable_bins_required,
    )
    gaps = gaps_from_bins(bins)
    gap_stats_dict = gap_stats(gaps, total_frames, fps)
    trusted_bins = sum(1 for b in bins if b.trust == TRUST_TRUSTED)
    wall_buckets = _wall_clock_buckets(segments, fps)

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
        "model": "ideal_vs_detected_1m",
        "bin_duration_s": bin_duration_s,
        "sync_tolerance_s": sync_tolerance_s,
        "stable_bins_required": stable_bins_required,
        "segments": segments,
        "num_segments": num_segments,
        "bins": [b.to_dict() for b in bins],
        "gaps": gap_dicts,
        "wall_clock_buckets": wall_buckets,
        "stats": {
            **gap_stats_dict,
            "num_bins": len(bins),
            "trusted_bins": trusted_bins,
            "trusted_fraction": round(trusted_bins / max(len(bins), 1), 4),
            "recovery_bins": sum(1 for b in bins if b.trust == TRUST_RECOVERY),
            "unstable_bins": sum(1 for b in bins if b.trust == TRUST_UNSTABLE),
            "missing_bins": sum(1 for b in bins if b.trust == TRUST_MISSING),
        },
    }


def frame_to_wall_epoch(
    frame_idx: int,
    sync_map: Dict[str, Any],
    fps: float,
) -> Optional[float]:
    """Map a frame index to wall-clock epoch using trusted segments."""
    if fps <= 0:
        return None
    t_s = frame_idx / fps
    for seg in sync_map.get("segments", []):
        start_f = int(seg["start_frame"])
        end_f = int(seg["end_frame"])
        if start_f <= frame_idx < end_f:
            anchor_epoch = float(seg["anchor_epoch"])
            anchor_t_s = float(seg["anchor_t_s"])
            return anchor_epoch + (t_s - anchor_t_s)
    return None