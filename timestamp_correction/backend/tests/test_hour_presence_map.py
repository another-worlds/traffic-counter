from datetime import datetime, timezone

from worker.coherence_map import (
    PRESENCE_MODEL,
    build_hour_presence_map,
    frame_to_wall_epoch,
)
from worker.timestamp_reader import TimestampSample


def _sample(frame_idx, t_s, epoch, present=True, text=""):
    return TimestampSample(
        frame_idx=frame_idx,
        t_seconds=t_s,
        wall_clock_epoch=epoch,
        ocr_text=text,
        present=present,
    )


def _day_start_epoch(date_str: str) -> float:
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return dt.timestamp()


def test_realtime_map_fully_parsed():
    day = "2024-03-15"
    base = _day_start_epoch(day)
    fps = 30.0
    start_wall = base + 7 * 3600
    n_min = 120
    samples = []
    for i in range(n_min):
        t_s = i * 60.0
        samples.append(_sample(int(t_s * fps), t_s, start_wall + t_s, text="ok"))
    total_frames = int(n_min * 60 * fps)
    doc = build_hour_presence_map(
        samples, fps, total_frames,
        date_mode="fixed",
        fixed_date=day,
        map_mode="realtime",
    )
    assert doc["model"] == PRESENCE_MODEL
    covered = [h for h in doc["hour_presence"] if h["minutes_sampled"] > 0]
    assert len(covered) == 2
    assert covered[0]["parsed_percent"] == 100.0
    assert all(b["present"] for b in doc["bins"])


def test_stretch_map_for_full_day_fit():
    day = "2024-03-15"
    base = _day_start_epoch(day)
    fps = 30.0
    n_min = 120
    video_duration_s = n_min * 60.0
    scale = 86400.0 / video_duration_s
    samples = []
    for i in range(n_min):
        t_s = i * 60.0
        ideal_at_t = base + t_s * scale
        samples.append(_sample(int(t_s * fps), t_s, ideal_at_t, text="ok"))
    total_frames = int(n_min * 60 * fps)
    doc = build_hour_presence_map(
        samples, fps, total_frames,
        date_mode="fixed",
        fixed_date=day,
        map_mode="stretch",
    )
    covered = [h for h in doc["hour_presence"] if h["minutes_sampled"] > 0]
    assert len(covered) == 24
    assert covered[0]["minutes_sampled"] == 5


def test_drift_still_counts_as_present():
    day = "2024-03-15"
    base = _day_start_epoch(day)
    fps = 30.0
    samples = [
        _sample(0, 0.0, base + 0.0),
        _sample(int(60 * fps), 60.0, base + 120.0),
    ]
    total_frames = int(2 * 60 * fps)
    doc = build_hour_presence_map(
        samples, fps, total_frames,
        date_mode="fixed",
        fixed_date=day,
        map_mode="realtime",
    )
    assert doc["bins"][0]["present"] is True
    assert doc["bins"][1]["present"] is True
    assert not any(g["reason"] == "timestamp_drift" for g in doc["gaps"])
    assert doc["stats"]["parsed_bins"] == 2


def test_missing_osd_unparsed_gap():
    day = "2024-03-15"
    base = _day_start_epoch(day)
    fps = 30.0
    samples = [
        _sample(0, 0.0, base),
        _sample(int(60 * fps), 60.0, None, present=False),
    ]
    total_frames = int(2 * 60 * fps)
    doc = build_hour_presence_map(samples, fps, total_frames, date_mode="fixed", fixed_date=day)
    assert doc["bins"][1]["present"] is False
    assert any(g["reason"] == "missing_osd" for g in doc["gaps"])
    hour = next(h for h in doc["hour_presence"] if h["minutes_sampled"] > 0)
    assert hour["parsed_percent"] == 50.0


def test_partial_parse_hour_metrics():
    day = "2024-03-15"
    base = _day_start_epoch(day)
    fps = 30.0
    samples = [
        _sample(0, 0.0, base),
        _sample(int(60 * fps), 60.0, None, present=False),
        _sample(int(120 * fps), 120.0, base + 120.0),
    ]
    total_frames = int(3 * 60 * fps)
    doc = build_hour_presence_map(samples, fps, total_frames, date_mode="fixed", fixed_date=day)
    hour = next(h for h in doc["hour_presence"] if h["minutes_sampled"] == 3)
    assert hour["minutes_present"] == 2
    assert hour["minutes_unparsed"] == 1
    assert round(hour["parsed_percent"], 1) == 66.7


def test_frame_to_wall_epoch_present_bins():
    day = "2024-03-15"
    base = _day_start_epoch(day)
    fps = 30.0
    samples = [_sample(0, 0.0, base), _sample(int(60 * fps), 60.0, base + 60.0)]
    total_frames = int(2 * 60 * fps)
    doc = build_hour_presence_map(samples, fps, total_frames, date_mode="fixed", fixed_date=day)
    epoch = frame_to_wall_epoch(0, doc, fps)
    assert epoch is not None
    assert abs(epoch - base) < 2.0