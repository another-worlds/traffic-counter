from __future__ import annotations

import pandas as pd

from app.services.timeline_viz import build_coverage_segments, build_timeline_visualization


def test_build_coverage_segments_merges_valid_and_gaps():
    gaps = [
        {"start_t_s": 10.0, "end_t_s": 20.0, "reason": "missing_osd", "start_frame": 250, "end_frame": 500},
        {"start_t_s": 50.0, "end_t_s": 55.0, "reason": "time_jump", "start_frame": 1250, "end_frame": 1375},
    ]
    segments = build_coverage_segments(gaps, total_duration_s=100.0)
    kinds = [s["kind"] for s in segments]
    assert kinds[0] == "valid"
    assert "missing_osd" in kinds
    assert "time_jump" in kinds
    assert kinds[-1] == "valid"


def test_build_timeline_visualization_includes_presence():
    df = pd.DataFrame({
        "frame_idx": [0, 100, 200, 300],
        "t_seconds": [0.0, 4.0, 8.0, 12.0],
        "present": [True, False, True, True],
    })
    gaps = [{"start_frame": 80, "end_frame": 120, "start_t_s": 3.2, "end_t_s": 4.8, "reason": "missing_osd"}]
    stats = {"total_duration_s": 12.0, "total_frames": 300, "gap_duration_s": 1.6, "num_gaps": 1, "by_reason": {"missing_osd": 1}}
    viz = build_timeline_visualization(gaps, stats, df, max_points=10)
    assert viz["num_gaps"] == 1
    assert len(viz["segments"]) >= 2
    assert len(viz["presence"]) == 4