"""
Locate the burned-in timestamp overlay region in CCTV frames.

Uses a small CNN (RegionLocatorNet) to score candidate corner/strip patches.
When no trained weights are available, falls back to OCR-validation bootstrap.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import cv2
import numpy as np
import torch
import torch.nn as nn

log = logging.getLogger("timestamp_correction.region_locator")

PATCH_H = 64
PATCH_W = 128


@dataclass
class Region:
    x: int
    y: int
    w: int
    h: int
    confidence: float
    method: str  # "cnn" | "ocr_bootstrap"


class RegionLocatorNet(nn.Module):
    """Scores a grayscale OSD patch for timestamp-likeness."""

    def __init__(self) -> None:
        super().__init__()
        self.features = nn.Sequential(
            nn.Conv2d(1, 16, 3, padding=1),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(2),
            nn.Conv2d(16, 32, 3, padding=1),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(2),
            nn.Conv2d(32, 64, 3, padding=1),
            nn.ReLU(inplace=True),
            nn.AdaptiveAvgPool2d((4, 8)),
        )
        self.head = nn.Sequential(
            nn.Flatten(),
            nn.Linear(64 * 4 * 8, 128),
            nn.ReLU(inplace=True),
            nn.Linear(128, 1),
            nn.Sigmoid(),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.head(self.features(x)).squeeze(-1)


def _heuristic_score(patch: np.ndarray) -> float:
    """Edge-density heuristic when CNN weights are unavailable."""
    if patch.size == 0:
        return 0.0
    gx = cv2.Sobel(patch, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(patch, cv2.CV_32F, 0, 1, ksize=3)
    mag = np.sqrt(gx * gx + gy * gy)
    edge_density = float(np.mean(mag > 30))
    # Timestamps have many horizontal strokes
    h_proj = np.mean(mag, axis=1)
    h_var = float(np.std(h_proj) / (np.mean(h_proj) + 1e-6))
    return min(1.0, 0.6 * edge_density + 0.4 * min(h_var, 1.0))


def _preprocess_patch(gray: np.ndarray) -> np.ndarray:
    h, w = gray.shape[:2]
    resized = cv2.resize(gray, (PATCH_W, PATCH_H), interpolation=cv2.INTER_AREA)
    return resized.astype(np.float32) / 255.0


def _candidate_regions(width: int, height: int) -> List[Tuple[int, int, int, int]]:
    """Typical CCTV OSD placements: corners and horizontal strips."""
    margin = max(8, int(min(width, height) * 0.02))
    rw = max(120, int(width * 0.22))
    rh = max(32, int(height * 0.06))
    candidates = [
        (margin, margin, rw, rh),                              # top-left
        (width - rw - margin, margin, rw, rh),                   # top-right
        (margin, height - rh - margin, rw, rh),                # bottom-left
        (width - rw - margin, height - rh - margin, rw, rh),   # bottom-right
        (margin, margin, width - 2 * margin, rh),              # top strip
        (margin, height - rh - margin, width - 2 * margin, rh),  # bottom strip
    ]
    out = []
    for x, y, w, h in candidates:
        x = max(0, min(x, width - 1))
        y = max(0, min(y, height - 1))
        w = min(w, width - x)
        h = min(h, height - y)
        if w >= 40 and h >= 20:
            out.append((x, y, w, h))
    return out


def _load_model(weights_path: str, device: str) -> Optional[RegionLocatorNet]:
    path = Path(weights_path)
    if not path.exists():
        return None
    model = RegionLocatorNet().to(device)
    model.load_state_dict(torch.load(path, map_location=device, weights_only=True))
    model.eval()
    return model


def _score_patch(
    gray: np.ndarray,
    bbox: Tuple[int, int, int, int],
    model: Optional[RegionLocatorNet],
    device: str,
) -> float:
    x, y, w, h = bbox
    crop = gray[y : y + h, x : x + w]
    if crop.size == 0:
        return 0.0
    if len(crop.shape) == 3:
        crop = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    patch = _preprocess_patch(crop)
    if model is not None:
        tensor = torch.from_numpy(patch).unsqueeze(0).unsqueeze(0).to(device)
        with torch.no_grad():
            return float(model(tensor).item())
    return _heuristic_score(patch)


def locate_region(
    frames: List[np.ndarray],
    weights_path: str,
    device: str = "cpu",
    ocr_scorer=None,
) -> Region:
    """
    Find the timestamp ROI by scoring candidate regions across sample frames.

    `ocr_scorer(crop_bgr) -> float` optional callback for bootstrap validation.
    """
    model = _load_model(weights_path, device)
    method = "cnn" if model is not None else "heuristic"

    h, w = frames[0].shape[:2]
    candidates = _candidate_regions(w, h)
    votes: dict[Tuple[int, int, int, int], List[float]] = {c: [] for c in candidates}

    for frame in frames:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if len(frame.shape) == 3 else frame
        for bbox in candidates:
            votes[bbox].append(_score_patch(gray, bbox, model, device))

    avg_scores = {b: float(np.mean(s)) for b, s in votes.items()}

    ocr_scores: dict[Tuple[int, int, int, int], float] = {}
    if ocr_scorer is not None:
        for bbox in candidates:
            x, y, bw, bh = bbox
            frame_scores: list[float] = []
            for frame in frames[: min(5, len(frames))]:
                crop = frame[y : y + bh, x : x + bw]
                if crop.size:
                    frame_scores.append(float(ocr_scorer(crop)))
            ocr_scores[bbox] = float(np.mean(frame_scores)) if frame_scores else 0.0

    if ocr_scores:
        combined: dict[Tuple[int, int, int, int], float] = {}
        for bbox in candidates:
            ocr = ocr_scores.get(bbox, 0.0)
            cnn = avg_scores[bbox]
            if ocr > 0:
                combined[bbox] = 0.85 * ocr + 0.15 * cnn
            else:
                combined[bbox] = 0.15 * cnn
        best_bbox = max(combined, key=combined.get)
        confidence = combined[best_bbox]
        ocr_best = ocr_scores.get(best_bbox, 0.0)
        if ocr_best >= 0.55:
            method = "ocr_timestamp" if model is None else "cnn+ocr_timestamp"
        elif ocr_best > 0:
            method = "ocr_timestamp_weak" if model is None else "cnn+ocr_timestamp_weak"
    else:
        best_bbox = max(avg_scores, key=avg_scores.get)
        confidence = avg_scores[best_bbox]

    x, y, bw, bh = best_bbox
    log.info("located timestamp region (%d,%d,%d,%d) conf=%.3f method=%s", x, y, bw, bh, confidence, method)
    return Region(x=x, y=y, w=bw, h=bh, confidence=confidence, method=method)