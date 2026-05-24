#!/usr/bin/env python3
"""Read-only diagnostic: prove or rule out frame-count / seek defects on a real file.

Runs on CPU with no model. It replicates the worker's *only* decode path
(cv2.VideoCapture + CAP_PROP_FRAME_COUNT, the same calls in worker/pipeline.py)
and answers two questions for a given source video:

  1. TAIL DROP — does the worker's frame-count estimate underreport the true
     decodable frame count?  The planner tiles [0, num_frames) into hour
     segments, so any frame past the estimate is assigned to no segment and is
     silently dropped from the vehicle count.
  2. SEEK ALIGNMENT — does cap.set(CAP_PROP_POS_FRAMES, N) land exactly on
     frame N?  A miss would double-count (lands early) or undercount (lands
     late) vehicles at each hour boundary.

Usage:
    python diag_framecount.py VIDEO_PATH [--seg-seconds 3600] [--probe-frame N]

Exit code is 0 regardless of verdict; read the printed VERDICT lines.
"""
import argparse
import shutil
import subprocess
import sys

import cv2
import numpy as np


def _ahash(frame) -> int:
    """Cheap 8x8 average hash for frame-identity comparison."""
    g = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    small = cv2.resize(g, (8, 8), interpolation=cv2.INTER_AREA)
    bits = (small > small.mean()).flatten()
    out = 0
    for b in bits:
        out = (out << 1) | int(b)
    return out


def _hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def decode_to_eof(path: str) -> int:
    """True decodable frame count: loop cap.read() to EOF (the worker's path)."""
    cap = cv2.VideoCapture(path)
    n = 0
    while True:
        ok, _ = cap.read()
        if not ok:
            break
        n += 1
    cap.release()
    return n


def ffprobe_count(path: str):
    if not shutil.which("ffprobe"):
        return None
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
             "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=3600,
        )
        txt = out.stdout.strip().splitlines()
        return int(txt[0]) if txt and txt[0].isdigit() else None
    except Exception as exc:  # noqa: BLE001
        print(f"  ffprobe failed: {exc}")
        return None


def check_tail(path: str) -> None:
    cap = cv2.VideoCapture(path)
    est = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    cap.release()

    truth = decode_to_eof(path)
    probe = ffprobe_count(path)

    print("== TAIL DROP ==")
    print(f"  fps                          : {fps:.4f}")
    print(f"  CAP_PROP_FRAME_COUNT (est)   : {est}   <- what the worker plans with")
    print(f"  decode-to-EOF (truth)        : {truth}")
    print(f"  ffprobe nb_read_frames       : {probe if probe is not None else 'n/a'}")

    if truth > est:
        dropped = truth - est
        print(f"  VERDICT: CONFIRMED tail drop. {dropped} frame(s) "
              f"(~{dropped / fps:.1f}s) past the estimate are silently lost. "
              f"The read-to-EOF fix recovers them.")
    elif truth < est:
        print("  VERDICT: estimate is inflated (truth < est); benign — the "
              "decoder hits EOF before reaching the planned end. No drop.")
    else:
        print("  VERDICT: estimate is exact; tail drop ruled out for this file.")
    if probe is not None and probe != truth:
        print(f"  NOTE: ffprobe ({probe}) disagrees with OpenCV decode ({truth}); "
              "the worker uses the OpenCV count, so 'truth' governs.")


def check_seek(path: str, n: int) -> None:
    print("== SEEK ALIGNMENT ==")
    if n <= 0:
        print("  skipped (probe frame <= 0).")
        return

    # Reference: sequential decode to frame n (ground-truth identity).
    cap = cv2.VideoCapture(path)
    ref = None
    for i in range(n + 1):
        ok, frame = cap.read()
        if not ok:
            print(f"  video has fewer than {n + 1} frames; skipping seek check.")
            cap.release()
            return
        if i == n:
            ref = frame
    cap.release()

    # Seek exactly as worker/pipeline.py _frame_generator does.
    cap = cv2.VideoCapture(path)
    cap.set(cv2.CAP_PROP_POS_FRAMES, n)
    landed = int(cap.get(cv2.CAP_PROP_POS_FRAMES))
    ok, seeked = cap.read()
    after = int(cap.get(cv2.CAP_PROP_POS_FRAMES))
    cap.release()
    if not ok:
        print(f"  read after seek to {n} failed.")
        return

    delta_pos = landed - n
    dist = _hamming(_ahash(ref), _ahash(seeked))
    print(f"  probe frame index            : {n}")
    print(f"  POS_FRAMES reported pre-read : {landed} (delta {delta_pos:+d})")
    print(f"  POS_FRAMES reported post-read: {after}")
    print(f"  ahash hamming(ref, seeked)   : {dist}/64")
    if dist <= 2 and abs(delta_pos) <= 0:
        print("  VERDICT: seek is frame-accurate; hour-boundary misalignment "
              "ruled out for this file.")
    else:
        print("  VERDICT: seek is INEXACT. A nonzero offset means each hour "
              "boundary double-counts (lands early) or undercounts (lands "
              "late). Consider seam de-dup by absolute frame_idx.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("video_path")
    ap.add_argument("--seg-seconds", type=float, default=3600.0,
                    help="segment duration the worker uses (default 3600).")
    ap.add_argument("--probe-frame", type=int, default=None,
                    help="frame index to test seeking (default: first hour boundary).")
    args = ap.parse_args()

    cap = cv2.VideoCapture(args.video_path)
    if not cap.isOpened():
        print(f"ERROR: could not open {args.video_path}")
        return 1
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    cap.release()

    probe = args.probe_frame
    if probe is None:
        probe = max(1, int(round(fps * args.seg_seconds)))

    print(f"file: {args.video_path}\n")
    check_tail(args.video_path)
    print()
    check_seek(args.video_path, probe)
    return 0


if __name__ == "__main__":
    sys.exit(main())
