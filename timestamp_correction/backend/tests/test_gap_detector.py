from worker.gap_detector import detect_gaps, gap_stats
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