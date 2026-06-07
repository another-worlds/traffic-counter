from worker.sync_map import build_sync_map, frame_to_wall_epoch
from worker.timestamp_reader import TimestampSample


def _sample(frame_idx, t_s, epoch, present=True, text=""):
    return TimestampSample(
        frame_idx=frame_idx,
        t_seconds=t_s,
        wall_clock_epoch=epoch,
        ocr_text=text,
        present=present,
    )


def _coherent_minutes(n: int, fps: float = 30.0, base_epoch: float = 1_000_000.0):
    samples = []
    for i in range(n):
        t_s = i * 60.0
        samples.append(_sample(int(t_s * fps), t_s, base_epoch + t_s))
    total_frames = int(n * 60 * fps)
    return samples, total_frames, fps


def test_stable_segment_after_three_coherent_bins():
    samples, total_frames, fps = _coherent_minutes(6)
    doc = build_sync_map(samples, fps, total_frames, stable_bins_required=3)
    assert doc["num_segments"] == 1
    assert doc["stats"]["trusted_bins"] == 6
    assert doc["stats"]["recovery_bins"] == 0
    assert len(doc["gaps"]) == 0


def test_join_triggers_recovery_and_new_segment():
    fps = 30.0
    samples = []
    for i in range(4):
        t_s = i * 60.0
        samples.append(_sample(int(t_s * fps), t_s, 1_000_000.0 + t_s))
    # jump: 20 minutes of wall clock missing across one video minute
    samples.append(_sample(int(4 * 60 * fps), 240.0, 1_000_000.0 + 240.0 + 1200.0))
    for i in range(5, 8):
        t_s = i * 60.0
        samples.append(_sample(int(t_s * fps), t_s, samples[-1].wall_clock_epoch + 60.0))

    total_frames = int(8 * 60 * fps)
    doc = build_sync_map(samples, fps, total_frames, stable_bins_required=3)
    assert doc["num_segments"] == 2
    unstable = [b for b in doc["bins"] if b["trust"] == "unstable"]
    recovery = [b for b in doc["bins"] if b["trust"] == "recovery"]
    assert len(unstable) >= 1
    assert len(recovery) >= 1
    assert any(g["reason"] in ("time_jump", "sync_recovery") for g in doc["gaps"])


def test_short_tail_stays_untrusted_when_fewer_than_stable_required():
    samples, total_frames, fps = _coherent_minutes(2)
    doc = build_sync_map(samples, fps, total_frames, stable_bins_required=3)
    assert doc["num_segments"] == 0
    assert doc["stats"]["trusted_bins"] == 0
    assert all(b["trust"] != "trusted" for b in doc["bins"])


def test_frame_to_wall_epoch_within_segment():
    samples, total_frames, fps = _coherent_minutes(5, base_epoch=2_000_000.0)
    doc = build_sync_map(samples, fps, total_frames, stable_bins_required=3)
    epoch = frame_to_wall_epoch(int(3.5 * 60 * fps), doc, fps)
    assert epoch is not None
    assert abs(epoch - (2_000_000.0 + 3.5 * 60.0)) < 2.0