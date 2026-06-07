from worker.timestamp_parse import enrich_samples, normalize_osd_text, parse_osd_datetime
from worker.timestamp_reader import TimestampSample


REAL_OCR = [
    ("2026 3 06 22 12", True),
    ("05:31.46", True),
    ("06 :03:54", True),
    ("20-05-2026", True),
    ("2026-05 06 09:05", True),
    ("26 05.24.21", True),
    ("05 35;36", True),
    ("2026 0 06 :7:39", True),
    ("'20-05-20z6 04:32;09", True),
    ("garbage", False),
]


def test_normalize_osd():
    assert ":" in normalize_osd_text("06 :03:54")
    assert normalize_osd_text("05.31.46") == "05:31:46"


def test_parse_real_ocr_samples():
    parsed = 0
    for text, should_parse in REAL_OCR:
        epoch, date, time_part = parse_osd_datetime(text)
        ok = epoch is not None or date is not None or time_part is not None
        if should_parse:
            assert ok, f"failed to parse {text!r}"
            parsed += 1
        else:
            assert not ok
    assert parsed >= 8


def test_carry_forward_date():
    samples = [
        TimestampSample(0, 0.0, None, "20-05-2026", False),
        TimestampSample(1500, 60.0, None, "06 :03:54", False),
        TimestampSample(3000, 120.0, None, "06 :05:12", False),
    ]
    out = enrich_samples(samples)
    assert out[0].present
    assert out[1].present
    assert out[2].present
    assert out[1].wall_clock_epoch < out[2].wall_clock_epoch