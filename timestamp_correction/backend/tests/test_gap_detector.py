from worker.gap_detector import GapInterval, detect_gaps, gap_stats, normalize_by_reason, primary_gap_reason
from worker.timestamp_reader import TimestampSample


def _sample(frame_idx, t_s, epoch, present=True, text=""):
    return TimestampSample(
        frame_idx=frame_idx,
        t_seconds=t_s,
        wall_clock_epoch=epoch,
        ocr_text=text,
        present=present,
    )


def test_missing_osd_gap():
    samples = [
        _sample(0, 0, 1000.0),
        _sample(150, 5, None, present=False),
        _sample(300, 10, None, present=False),
        _sample(450, 15, None, present=False),
        _sample(600, 20, 1020.0),
    ]
    gaps = detect_gaps(samples, fps=30, sample_interval_s=5, min_gap_samples=3)
    assert len(gaps) == 1
    assert gaps[0].reason == "missing_osd"
    assert gaps[0].start_frame == 150


def test_time_jump_gap():
    samples = [
        _sample(0, 0, 1000.0),
        _sample(150, 5, 1005.0),
        _sample(300, 10, 1100.0),  # 95s jump in 5s of footage
        _sample(450, 15, 1105.0),
    ]
    gaps = detect_gaps(samples, fps=30, sample_interval_s=5, jump_threshold_s=15)
    assert any(g.reason == "time_jump" for g in gaps)


def test_time_reverse_gap():
    samples = [
        _sample(0, 0, 2000.0),
        _sample(150, 5, 2005.0),
        _sample(300, 10, 1990.0),
    ]
    gaps = detect_gaps(samples, fps=30, sample_interval_s=5)
    assert any(g.reason == "time_reverse" for g in gaps)


def test_gap_stats():
    gaps = detect_gaps([
        _sample(0, 0, 1000.0),
        _sample(150, 5, None, present=False),
        _sample(300, 10, None, present=False),
        _sample(450, 15, None, present=False),
    ], fps=30, sample_interval_s=5, min_gap_samples=3)
    stats = gap_stats(gaps, total_frames=900, fps=30)
    assert stats["num_gaps"] == 1
    assert stats["gap_fraction"] > 0


def test_merge_adjacent_same_reason_extends_interval():
    from worker.gap_detector import _merge_overlapping

    gaps = _merge_overlapping([
        GapInterval(0, 100, 0.0, 3.0, "sync_recovery"),
        GapInterval(100, 200, 3.0, 6.0, "sync_recovery"),
    ])
    assert len(gaps) == 1
    assert gaps[0].reason == "sync_recovery"
    assert gaps[0].end_frame == 200


def test_merge_adjacent_different_reasons_stay_separate():
    from worker.gap_detector import _merge_overlapping

    gaps = _merge_overlapping([
        GapInterval(0, 100, 0.0, 3.0, "sync_recovery"),
        GapInterval(100, 200, 3.0, 6.0, "missing_osd"),
    ])
    assert len(gaps) == 2
    assert gaps[0].reason == "sync_recovery"
    assert gaps[1].reason == "missing_osd"


def test_merge_true_overlap_uses_primary_reason():
    from worker.gap_detector import _merge_overlapping

    gaps = _merge_overlapping([
        GapInterval(0, 150, 0.0, 5.0, "sync_recovery"),
        GapInterval(100, 200, 3.0, 6.0, "missing_osd"),
    ])
    assert len(gaps) == 1
    assert gaps[0].reason == "missing_osd"


def test_normalize_by_reason_collapses_legacy_compound_keys():
    assert primary_gap_reason("sync_recovery+missing_osd+time_jump") == "missing_osd"
    assert normalize_by_reason({
        "sync_recovery+missing_osd": 3,
        "time_jump": 7,
        "missing_osd": 22,
    }) == {"missing_osd": 25, "time_jump": 7}