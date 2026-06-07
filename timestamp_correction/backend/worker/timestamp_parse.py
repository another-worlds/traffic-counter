"""
Flexible OSD datetime parsing for noisy EasyOCR output on CCTV timestamps.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import List, Optional, Tuple

# Strict patterns (original)
_STRICT_PATTERNS = [
    re.compile(r"(\d{4})[-/.](\d{2})[-/.](\d{2})\s+(\d{2}):(\d{2}):(\d{2})"),
    re.compile(r"(\d{2})[-/.](\d{2})[-/.](\d{4})\s+(\d{2}):(\d{2}):(\d{2})"),
    re.compile(r"(\d{2}):(\d{2}):(\d{2})"),
]

# Relaxed patterns for OCR noise
_RELAXED_PATTERNS = [
    # 2026-05 06 09:05 or 2026 05 06 09:05:03
    re.compile(
        r"(\d{4})[\s\-_/]+(\d{1,2})[\s\-_/]+(\d{1,2})[\s]+(\d{1,2}):(\d{2})(?::(\d{2}))?"
    ),
    # 2026 3 06 22 12 34 (space-separated, optional seconds)
    re.compile(r"(\d{4})\s+(\d{1,2})\s+(\d{1,2})\s+(\d{1,2})\s+(\d{1,2})(?:\s+(\d{1,2}))?"),
    # DD-MM-YYYY with optional time
    re.compile(
        r"(\d{2})[\s\-/.]+(\d{2})[\s\-/.]+(\d{4})(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?"
    ),
    # DD-MM-YY (2-digit year)
    re.compile(
        r"(\d{2})[\s\-/.]+(\d{2})[\s\-/.]+(\d{2})(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?"
    ),
    # time only with flexible separators: 05:31.46, 06 :03:54
    re.compile(r"(?<!\d)(\d{1,2})[\s:.](\d{2})[\s:.](\d{2})(?!\d)"),
    # date only DD-MM-YYYY
    re.compile(r"(?<!\d)(\d{2})[\s\-/.](\d{2})[\s\-/.](\d{4})(?!\d)"),
    re.compile(r"(?<!\d)(\d{4})[\s\-/.](\d{1,2})[\s\-/.](\d{1,2})(?!\d)"),
]


def normalize_osd_text(text: str) -> str:
    """Clean common OCR substitutions and separator noise."""
    t = str(text).strip()
    if not t:
        return ""

    # Drop obvious non-timestamp prefix junk (channel names etc.) when digits follow.
    t = re.sub(r"^[^\d]{0,12}(?=\d)", "", t)

    trans = str.maketrans({
        "O": "0", "o": "0", "Q": "0", "D": "0",
        "l": "1", "I": "1", "|": "1", "!": "1",
        "Z": "2", "z": "2",
        "S": "5", "s": "5",
        "B": "8", "G": "6", "E": "6", "q": "9",
        ";": ":", ",": ":", "*": "", "_": " ",
    })
    t = t.translate(trans)
    t = re.sub(r"[^\d/:.\-\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    # 05.31.46 → 05:31:46
    t = re.sub(r"(\d{1,2})\.(\d{2})\.(\d{2})", r"\1:\2:\3", t)
    t = re.sub(r"(\d{1,2})\.(\d{2}):(\d{2})", r"\1:\2:\3", t)
    t = re.sub(r"(\d{1,2}):(\d{2})\.(\d{2})", r"\1:\2:\3", t)
    t = re.sub(r"\s*:\s*", ":", t)
    t = re.sub(r"\s*-\s*", "-", t)
    return t


_MIN_YEAR = 2015
_MAX_YEAR = 2035


def _epoch_from_parts(
    year: int,
    month: int,
    day: int,
    hour: int,
    minute: int,
    second: int = 0,
) -> Optional[float]:
    if not (_MIN_YEAR <= year <= _MAX_YEAR):
        return None
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59):
        return None
    try:
        dt = datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        return None


def _expand_year(y: int) -> int:
    if y < 100:
        return 2000 + y if y < 70 else 1900 + y
    return y


def _parse_match(groups: Tuple[str, ...]) -> Tuple[Optional[float], Optional[Tuple[int, int, int]]]:
    """Return (epoch, date_tuple) — date_tuple set when a calendar date was parsed."""
    g = [x for x in groups if x is not None]
    if not g:
        return None, None

    try:
        nums = [int(x) for x in g]
    except ValueError:
        return None, None

    # 6 parts: full datetime
    if len(nums) == 6:
        if nums[0] > 1900:  # YYYY-MM-DD HH:MM:SS
            ep = _epoch_from_parts(nums[0], nums[1], nums[2], nums[3], nums[4], nums[5])
            return ep, (nums[0], nums[1], nums[2]) if ep else None
        # DD-MM-YYYY HH:MM:SS
        ep = _epoch_from_parts(nums[2], nums[1], nums[0], nums[3], nums[4], nums[5])
        return ep, (nums[2], nums[1], nums[0]) if ep else None

    # 5 parts: could be YYYY M D H M or DD MM YYYY H M
    if len(nums) == 5:
        if nums[0] > 1900:
            ep = _epoch_from_parts(nums[0], nums[1], nums[2], nums[3], nums[4], 0)
            return ep, (nums[0], nums[1], nums[2]) if ep else None
        if nums[2] > 1900:
            ep = _epoch_from_parts(nums[2], nums[1], nums[0], nums[3], nums[4], 0)
            return ep, (nums[2], nums[1], nums[0]) if ep else None
        # time only HH MM SS MM? unlikely — try as H M S with today
        return None, None

    # 4 parts with year in third position: DD MM YYYY [H] or YYYY M D H
    if len(nums) == 4:
        if nums[0] > 1900:
            ep = _epoch_from_parts(nums[0], nums[1], nums[2], nums[3], 0, 0)
            return ep, (nums[0], nums[1], nums[2]) if ep else None
        if nums[2] > 1900:
            ep = _epoch_from_parts(nums[2], nums[1], nums[0], nums[3], 0, 0)
            return ep, (nums[2], nums[1], nums[0]) if ep else None

    # 3 parts: YYYY M D or DD MM YYYY date only (before time-only — day can be < 24)
    if len(nums) == 3:
        if nums[0] > 1900:
            ep = _epoch_from_parts(nums[0], nums[1], nums[2], 0, 0, 0)
            return ep, (nums[0], nums[1], nums[2]) if ep else None
        if nums[2] > 1900:
            ep = _epoch_from_parts(nums[2], nums[1], nums[0], 0, 0, 0)
            return ep, (nums[2], nums[1], nums[0]) if ep else None
        if nums[0] < 24 and nums[1] < 60 and nums[2] < 60:
            return None, None  # time only — caller applies carry-forward date

    return None, None


def parse_osd_datetime(text: str) -> Tuple[Optional[float], Optional[Tuple[int, int, int]], Optional[Tuple[int, int, int]]]:
    """
    Parse OCR text. Returns (epoch, date_tuple, time_tuple).
    time_tuple is (H,M,S) when only time parsed; date_tuple when date found.
    """
    cleaned = normalize_osd_text(text)
    if not cleaned:
        return None, None, None

    for pat in _STRICT_PATTERNS + _RELAXED_PATTERNS:
        m = pat.search(cleaned)
        if not m:
            continue
        epoch, date = _parse_match(m.groups())
        if epoch is not None:
            return epoch, date, None

    # Time-only fallback: pick the last plausible HH:MM:SS substring.
    for m in re.finditer(r"(?<!\d)(\d{1,2}):(\d{2}):(\d{2})(?!\d)", cleaned):
        h, mi, s = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if h < 24 and mi < 60 and s < 60:
            return None, None, (h, mi, s)

    for m in re.finditer(r"(?<!\d)(\d{1,2}):(\d{2})(?!\d)", cleaned):
        h, mi = int(m.group(1)), int(m.group(2))
        if h < 24 and mi < 60:
            return None, None, (h, mi, 0)

    # Digit-run heuristic: YYYY M D H M [S]
    nums = [int(x) for x in re.findall(r"\d+", cleaned)]
    if len(nums) >= 5 and nums[0] > 1900:
        sec = nums[5] if len(nums) > 5 else 0
        ep = _epoch_from_parts(nums[0], nums[1], nums[2], nums[3], nums[4], sec)
        if ep is not None:
            return ep, (nums[0], nums[1], nums[2]), None

    if len(nums) == 3 and nums[0] > 1900:
        ep = _epoch_from_parts(nums[0], nums[1], nums[2], 0, 0, 0)
        if ep is not None:
            return ep, (nums[0], nums[1], nums[2]), None

    if len(nums) == 3:
        if nums[2] > 1900:
            ep = _epoch_from_parts(nums[2], nums[1], nums[0], 0, 0, 0)
            if ep is not None:
                return ep, (nums[2], nums[1], nums[0]), None
        if nums[0] > 1900:
            ep = _epoch_from_parts(nums[0], nums[1], nums[2], 0, 0, 0)
            if ep is not None:
                return ep, (nums[0], nums[1], nums[2]), None
        if nums[0] < 24 and nums[1] < 60 and nums[2] < 60:
            return None, None, (nums[0], nums[1], nums[2])

    return None, None, None


def enrich_samples(samples: List) -> List:
    """Apply carry-forward date to time-only OCR reads. Mutates/returns TimestampSample list."""
    last_date: Optional[Tuple[int, int, int]] = None
    enriched = []
    for s in samples:
        epoch = s.wall_clock_epoch
        if epoch is None and s.ocr_text:
            ep, date, time_part = parse_osd_datetime(s.ocr_text)
            if ep is not None:
                epoch = ep
                if date is not None:
                    last_date = date
            elif date is not None:
                last_date = date
            elif time_part is not None and last_date is not None:
                epoch = _epoch_from_parts(
                    last_date[0], last_date[1], last_date[2],
                    time_part[0], time_part[1], time_part[2] if len(time_part) > 2 else 0,
                )

        from .timestamp_reader import TimestampSample
        enriched.append(TimestampSample(
            frame_idx=s.frame_idx,
            t_seconds=s.t_seconds,
            wall_clock_epoch=epoch,
            ocr_text=s.ocr_text,
            present=epoch is not None,
        ))
    return enriched