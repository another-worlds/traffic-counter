"""Timeline OCR samples every 5 minutes of footage, not every frame."""
from __future__ import annotations

import numpy as np

from worker.region_locator import Region
from worker import timestamp_reader
from worker.timestamp_reader import compute_sample_stride_frames


def test_stride_scales_with_fps():
    assert compute_sample_stride_frames(25.0, 300.0) == 7500
    assert compute_sample_stride_frames(30.0, 300.0) == 9000
    assert compute_sample_stride_frames(50.0, 300.0) == 15000


def test_sample_stride_every_five_minutes(monkeypatch):
    region = Region(x=0, y=0, w=40, h=20, confidence=1.0, method="test")
    fps = 25.0
    interval_s = 300.0  # 5 minutes
    interval_frames = compute_sample_stride_frames(fps, interval_s)
    total_frames = interval_frames * 3 + 1

    def fake_ocr(_crop: np.ndarray):
        return 1_700_000_000.0, "2024-01-01 12:00:00"

    monkeypatch.setattr(timestamp_reader, "read_timestamp_from_crop", fake_ocr)

    class FakeCap:
        def __init__(self):
            self._pos = 0
            self.grab_calls = 0
            self.read_calls = 0

        def grab(self):
            self.grab_calls += 1
            if self._pos >= total_frames:
                return False
            self._pos += 1
            return True

        def read(self):
            self.read_calls += 1
            if self._pos >= total_frames:
                return False, None
            frame = np.zeros((480, 640, 3), dtype=np.uint8)
            self._pos += 1
            return True, frame

    cap = FakeCap()
    samples = timestamp_reader.sample_timeline(
        cap, region, fps=fps, sample_interval_s=interval_s, total_frames=total_frames,
    )

    assert len(samples) == 4
    assert [s.frame_idx for s in samples] == [0, interval_frames, interval_frames * 2, interval_frames * 3]
    assert cap.read_calls == 4
    assert cap.grab_calls == total_frames - 4