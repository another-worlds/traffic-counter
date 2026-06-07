"""Resolve DEVICE config to an available torch device (CUDA with CPU fallback)."""
from __future__ import annotations

import logging

log = logging.getLogger("timestamp_correction.device")


def resolve_torch_device(requested: str) -> str:
    import torch

    req = (requested or "cpu").strip()
    if req.startswith("cuda"):
        if torch.cuda.is_available():
            if req == "cuda":
                return "cuda:0"
            return req
        log.warning("DEVICE=%s requested but CUDA is unavailable — falling back to cpu", req)
    return "cpu"


def ocr_use_gpu(requested: str) -> bool:
    return resolve_torch_device(requested).startswith("cuda")


def log_device_status(requested: str) -> str:
    import torch

    resolved = resolve_torch_device(requested)
    if resolved.startswith("cuda"):
        idx = int(resolved.split(":")[1]) if ":" in resolved else 0
        name = torch.cuda.get_device_name(idx)
        log.info("timestamp GPU enabled: %s (%s)", resolved, name)
        return resolved
    log.info("timestamp running on cpu (requested DEVICE=%s)", requested)
    return resolved