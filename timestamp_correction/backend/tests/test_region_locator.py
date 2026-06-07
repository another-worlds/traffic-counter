"""Region locator prefers timestamp-shaped OCR over plain words."""
from __future__ import annotations

import numpy as np

from worker.region_locator import locate_region


def _frame_with_osd_markers() -> np.ndarray:
    """Bottom-right mimics a timestamp; top-left mimics a camera label."""
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    frame[12:44, 12:152] = 40
    frame[436:468, 488:628] = 220
    return frame


def test_locate_region_prefers_timestamp_ocr_over_words():
    frames = [_frame_with_osd_markers() for _ in range(3)]

    def scorer(crop: np.ndarray) -> float:
        mean = float(crop.mean())
        if mean >= 150:
            return 1.0
        if mean >= 20:
            return 0.0
        return 0.0

    region = locate_region(
        frames,
        weights_path="/nonexistent/weights.pt",
        ocr_scorer=scorer,
    )
    assert region.x >= 400
    assert region.y >= 400
    assert "ocr_timestamp" in region.method