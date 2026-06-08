"""
xlsx export. One workbook per export request:
  - "Сводка" sheet: every counting line, total + per-direction + per-class.
  - One sheet per counting line with two stacked direction reports
    ("Прямое" / "Обратное"), each showing per-segment (or full-video) rows
    with per-class columns.
"""
from __future__ import annotations
import io
from typing import List, Dict, Optional, Tuple
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

import pandas as pd

from .counting import (
    materialize_tracks,
    compute_counts_for_lines,
    COCO_VEHICLE_CLASSES,
)
from .tracks import load_materialized_tracks, load_tracks_for_video, load_tracks_for_videos


HEADER_FONT = Font(bold=True, color="FFFFFF")
HEADER_FILL = PatternFill("solid", fgColor="0C447C")

# Stable column order for all per-line sheets.
CLASS_ORDER: List[Tuple[str, str]] = [
    ("car",        "Легковые"),
    ("truck",      "Грузовые"),
    ("bus",        "Автобусы"),
    ("bicycle",    "Велосипеды"),
    ("motorcycle", "Мотоциклы"),
]

DIRECTION_LABEL = {"positive": "Прямое", "negative": "Обратное"}


def _write_header(ws, headers, row=1):
    for j, h in enumerate(headers, start=1):
        c = ws.cell(row=row, column=j, value=h)
        c.font = HEADER_FONT
        c.fill = HEADER_FILL
        c.alignment = Alignment(horizontal="center")


def _autosize(ws):
    for col_cells in ws.columns:
        col_letter = get_column_letter(col_cells[0].column)
        width = max((len(str(c.value)) if c.value is not None else 0) for c in col_cells)
        ws.column_dimensions[col_letter].width = min(max(width + 2, 10), 40)


def _fmt_time(secs: float) -> str:
    h = int(secs // 3600)
    m = int((secs % 3600) // 60)
    return f"{h}:{m:02d}"


def _safe_sheet_title(name: str, existing: set) -> str:
    safe = "".join(c for c in name if c not in r'/\?*[]:').strip() or "Линия"
    base = safe[:31]
    candidate = base
    i = 1
    while candidate in existing:
        i += 1
        candidate = f"{safe[:28]}_{i}"
    return candidate


def _write_direction_block(
    ws,
    start_row: int,
    direction: str,
    segment_rows: List[Dict],  # [{"label": str, "counts": {cls: int}, "total": int}]
) -> int:
    """Write one direction block to *ws* starting at *start_row*. Returns next free row."""
    label = f"{DIRECTION_LABEL[direction]} направление"
    ws.cell(row=start_row, column=1, value=label).font = Font(bold=True, size=11)
    r = start_row + 1

    col_headers = ["Временной интервал", "Всего"] + [ru for _, ru in CLASS_ORDER]
    _write_header(ws, col_headers, row=r)
    r += 1

    totals_total = 0
    totals_cls = {key: 0 for key, _ in CLASS_ORDER}

    for seg in segment_rows:
        row_vals = [seg["label"], seg["total"]] + [seg["counts"].get(key, 0) for key, _ in CLASS_ORDER]
        for j, val in enumerate(row_vals, start=1):
            ws.cell(row=r, column=j, value=val)
        totals_total += seg["total"]
        for key, _ in CLASS_ORDER:
            totals_cls[key] += seg["counts"].get(key, 0)
        r += 1

    # Итого row
    c0 = ws.cell(row=r, column=1, value="Итого")
    c0.font = Font(bold=True)
    c1 = ws.cell(row=r, column=2, value=totals_total)
    c1.font = Font(bold=True)
    for j, (key, _) in enumerate(CLASS_ORDER, start=3):
        c = ws.cell(row=r, column=j, value=totals_cls[key])
        c.font = Font(bold=True)
    r += 1
    return r


def _build_seg_entries(
    tracks_df,
    lines: List[Dict],
    segments: Optional[List[Dict]],
    full_counts_per_line: List[Dict],
) -> List[Tuple[str, Dict]]:
    """Build (label, {line_id: by_class_direction}) entries with O(S) materializations.

    Materializes each segment slice ONCE and computes all lines + both directions
    in a single compute_counts_for_lines call — matching the cost of the old
    per-hour export regardless of line count.

    When no segments are provided, returns a single "Всё видео" entry derived
    from the already-computed full-video counts (zero extra work).
    """
    import pandas as _pd

    is_df = isinstance(tracks_df, _pd.DataFrame)

    if not segments:
        line_map = {ln["line_id"]: ln["by_class_direction"] for ln in full_counts_per_line}
        return [("Всё видео", line_map)]

    entries = []
    for seg in sorted(segments, key=lambda s: s["segment_idx"]):
        t0 = float(seg.get("start_time_s", 0))
        t1 = float(seg.get("end_time_s", t0 + 3600))
        label = seg.get("label") or f"{_fmt_time(t0)}–{_fmt_time(t1)}"

        if is_df and not tracks_df.empty:
            if "start_frame" in seg and "end_frame" in seg:
                f0, f1 = int(seg["start_frame"]), int(seg["end_frame"])
                mask = (tracks_df["frame_idx"] >= f0) & (tracks_df["frame_idx"] < f1)
            else:
                mask = (tracks_df["t_seconds"] >= t0) & (tracks_df["t_seconds"] < t1)
            seg_df = tracks_df[mask]
        else:
            seg_df = _pd.DataFrame(columns=tracks_df.columns if is_df else [
                "frame_idx", "t_seconds", "track_id", "class_id", "conf", "cx", "cy", "w", "h",
            ])

        seg_mt = materialize_tracks(seg_df)
        seg_counts = compute_counts_for_lines(seg_mt, lines)
        line_map = {ln["line_id"]: ln["by_class_direction"] for ln in seg_counts["per_line"]}
        entries.append((label, line_map))

    return entries


REASON_LABELS = {
    "missing_osd": "Нет метки времени (OSD)",
    "time_jump": "Скачок времени",
    "time_reverse": "Обратный ход времени",
    "frozen_osd": "Замороженная метка",
    "sync_recovery": "Стабилизация после склейки",
    "timestamp_drift": "Расхождение с идеальной шкалой",
}


def _write_hour_presence_sheet(wb, gap_map: Dict, existing_titles: set) -> None:
    """Per-hour OSD presence on the ideal day."""
    hours = gap_map.get("hour_presence") or gap_map.get("hour_coherence") or []
    if not hours:
        return
    title = "Часы OSD"
    if title in existing_titles:
        title = "Часы OSD_2"
    existing_titles.add(title)
    ws = wb.create_sheet(title=title)
    ws.cell(row=1, column=1, value="Присутствие меток времени по часам").font = Font(bold=True, size=14)
    ideal = gap_map.get("ideal_day") or {}
    if ideal:
        ws.cell(row=2, column=1, value=(
            f"Идеальный день: {ideal.get('date', '—')} "
            f"{ideal.get('start', '')}–{ideal.get('end', '')}"
        ))
    headers = [
        "Час (UTC)",
        "Минут видео в часе",
        "Минут с OSD",
        "% распознано в фрагменте",
        "% покрытия идеального часа",
        "% OSD от 60 мин идеала",
    ]
    _write_header(ws, headers, row=4)
    r = 5
    for h in hours:
        sampled = int(h.get("minutes_sampled") or 0)
        present = int(h.get("minutes_present") or 0)
        parsed_pct = h.get("parsed_percent")
        if parsed_pct is None and sampled > 0:
            parsed_pct = round(100.0 * present / sampled, 1)
        ideal_pct = h.get("parsed_of_ideal_hour_percent", h.get("coherence_of_hour_percent"))
        if ideal_pct is None:
            ideal_pct = round(100.0 * present / 60.0, 1)
        row_vals = [
            h.get("hour_label"),
            sampled,
            present,
            parsed_pct,
            h.get("coverage_percent"),
            ideal_pct,
        ]
        for j, val in enumerate(row_vals, start=1):
            ws.cell(row=r, column=j, value=val)
        r += 1
    _autosize(ws)


def _write_hour_coherence_sheet(wb, gap_map: Dict, existing_titles: set) -> None:
    """Deprecated alias."""
    _write_hour_presence_sheet(wb, gap_map, existing_titles)


def _write_gaps_sheet(wb, gap_map: Dict, rows_excluded: int, existing_titles: set) -> None:
    """Log detected timestamp inconsistencies on a dedicated sheet."""
    title = "Несоответствия"
    if title in existing_titles:
        title = "Несоответствия_2"
    existing_titles.add(title)
    ws = wb.create_sheet(title=title)

    stats = gap_map.get("stats", {})
    ws.cell(row=1, column=1, value="Анализ меток времени (timestamp correction)").font = Font(bold=True, size=14)
    _write_header(ws, ["Показатель", "Значение"], row=3)

    summary_rows = [
        ("Количество разрывов", stats.get("num_gaps", 0)),
        ("Доля видео в разрывах", f"{100 * stats.get('gap_fraction', 0):.1f}%"),
        ("Длительность разрывов (с)", stats.get("gap_duration_s", 0)),
        ("Исключено строк треков", rows_excluded),
    ]
    if stats.get("hour_presence_map_enabled") or stats.get("coherence_map_enabled") or stats.get("sync_map_enabled"):
        parsed_frac = stats.get("parsed_fraction", stats.get("coherent_fraction", stats.get("trusted_fraction")))
        parsed_bins = stats.get("parsed_bins", stats.get("coherent_bins", stats.get("trusted_bins", "—")))
        summary_rows.extend([
            ("Распознанных 1-мин интервалов", parsed_bins),
            ("Доля распознанного timeline", (
                f"{100 * float(parsed_frac or 0):.1f}%"
                if parsed_frac is not None else "—"
            )),
            ("Часов с покрытием", stats.get("hours_with_coverage", "—")),
        ])
    by_reason = stats.get("by_reason", {})
    for reason, count in by_reason.items():
        label = REASON_LABELS.get(reason, reason)
        summary_rows.append((f"Причина: {label}", count))

    r = 4
    for label, val in summary_rows:
        ws.cell(row=r, column=1, value=label)
        ws.cell(row=r, column=2, value=val)
        r += 1

    r += 1
    gap_headers = ["Кадр нач.", "Кадр кон.", "Время нач. (с)", "Время кон. (с)", "Причина"]
    _write_header(ws, gap_headers, row=r)
    r += 1
    for g in gap_map.get("gaps", []):
        reason = REASON_LABELS.get(g.get("reason", ""), g.get("reason", ""))
        row_vals = [
            g.get("start_frame"),
            g.get("end_frame"),
            round(float(g.get("start_t_s", 0)), 1),
            round(float(g.get("end_t_s", 0)), 1),
            reason,
        ]
        for j, val in enumerate(row_vals, start=1):
            ws.cell(row=r, column=j, value=val)
        r += 1

    _autosize(ws)


def build_xlsx_for_video(
    project_name: str,
    video_filename: str,
    tracks_df,
    lines: List[Dict],
    segments: Optional[List[Dict]] = None,
    gap_map: Optional[Dict] = None,
    rows_excluded: int = 0,
    raw_tracks_df=None,
    wall_clock_segments: Optional[List[Dict]] = None,
) -> bytes:
    """Generate per-line workbook bytes.

    Sheet 1 "Сводка": summary row per line.
    Sheets 2..N: one per counting line with Прямое / Обратное direction blocks.
    """
    wb = Workbook()

    # ── Sheet 1: Сводка ──────────────────────────────────────────────────────
    ws = wb.active
    ws.title = "Сводка"
    ws.cell(row=1, column=1, value=f"Проект: {project_name}").font = Font(bold=True, size=14)
    ws.cell(row=2, column=1, value=f"Видео: {video_filename}")
    if gap_map:
        note = "⚠ Скорректировано по разрывам меток времени"
        if wall_clock_segments:
            note += f" · {len(wall_clock_segments)} UTC hour(s)"
        ws.cell(row=3, column=1, value=note).font = Font(
            bold=True, color="C65911"
        )

    corrected = bool(gap_map)
    raw_counts = None
    if corrected and raw_tracks_df is not None:
        raw_counts = compute_counts_for_lines(raw_tracks_df, lines)
    full_counts = compute_counts_for_lines(tracks_df, lines)

    if corrected:
        svod_headers = [
            "Линия", "Исходно", "Скоррект.", "Исключено",
            "Прямое", "Обратное",
            *[ru for _, ru in CLASS_ORDER],
        ]
        header_row = 4
    else:
        svod_headers = [
            "Линия", "Всего", "Прямое", "Обратное",
            *[ru for _, ru in CLASS_ORDER],
        ]
        header_row = 4
    _write_header(ws, svod_headers, row=header_row)

    raw_by_id = {}
    if raw_counts:
        raw_by_id = {ln["line_id"]: ln for ln in raw_counts["per_line"]}

    r = header_row + 1
    for ln in full_counts["per_line"]:
        bdir = ln["by_direction"]
        bcls = ln["by_class"]
        if corrected:
            raw_ln = raw_by_id.get(ln["line_id"], {})
            raw_total = raw_ln.get("total", ln["total"])
            row_vals = [
                ln["line_name"],
                raw_total,
                ln["total"],
                raw_total - ln["total"],
                bdir.get("positive", 0),
                bdir.get("negative", 0),
                *[bcls.get(key, 0) for key, _ in CLASS_ORDER],
            ]
        else:
            row_vals = [
                ln["line_name"],
                ln["total"],
                bdir.get("positive", 0),
                bdir.get("negative", 0),
                *[bcls.get(key, 0) for key, _ in CLASS_ORDER],
            ]
        for j, val in enumerate(row_vals, start=1):
            ws.cell(row=r, column=j, value=val)
        r += 1

    ws.cell(row=r, column=1, value="Итого уникальных треков").font = Font(bold=True)
    if corrected and raw_counts:
        ws.cell(row=r, column=2, value=raw_counts["total_unique_tracks"]).font = Font(bold=True)
        ws.cell(row=r, column=3, value=full_counts["total_unique_tracks"]).font = Font(bold=True)
        ws.cell(row=r, column=4, value=rows_excluded).font = Font(bold=True)
    else:
        ws.cell(row=r, column=2, value=full_counts["total_unique_tracks"]).font = Font(bold=True)
    _autosize(ws)

    existing_titles: set = {ws.title}
    if gap_map:
        _write_gaps_sheet(wb, gap_map, rows_excluded, existing_titles)
        _write_hour_presence_sheet(wb, gap_map, existing_titles)

    # Pre-pass: 1 materialization per segment (O(S) total, independent of line count).
    export_segments = wall_clock_segments if wall_clock_segments else segments
    seg_entries = _build_seg_entries(tracks_df, lines, export_segments, full_counts["per_line"])

    # ── Sheets per line — no materialization in this loop ────────────────────
    for line in lines:
        title = _safe_sheet_title(line.get("name", "Линия"), existing_titles)
        existing_titles.add(title)
        wsv = wb.create_sheet(title=title)

        wsv.cell(row=1, column=1, value=line.get("name", title)).font = Font(bold=True, size=12)

        next_row = 3
        for direction in ("positive", "negative"):
            seg_rows = [
                {
                    "label": label,
                    "counts": line_map.get(line["id"], {}).get(direction, {}),
                    "total": sum(line_map.get(line["id"], {}).get(direction, {}).values()),
                }
                for label, line_map in seg_entries
            ]
            next_row = _write_direction_block(wsv, next_row, direction, seg_rows)
            next_row += 1  # blank separator between blocks

        _autosize(wsv)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.getvalue()
