#!/usr/bin/env python3
"""Table-driven unit tests for _health.classify (stdlib unittest, no app needed).

Run: python worker/tools/test_health.py
"""
import unittest

import _health
from _health import classify, classify_liveness, DEFAULT_CFG, severity


def _vid(**kw):
    base = {"video_id": "v1", "filename": "x.mp4", "status": "analyzing",
            "progress_pct": 0.2, "speed_ratio": 2.0,
            "completed_segments": 2, "current_segment_idx": 2, "total_segments": 8}
    base.update(kw)
    return base


def _hist(n, epoch0, vid="v1", done=2, seg=2, progress=0.2, gpu=0, step=5):
    """n samples with a constant progress key (a flat/stalled video)."""
    return [
        {"epoch": epoch0 + i * step, "gpu_util": gpu,
         "vids": {vid: {"speed": 2.0, "done": done, "total": 8,
                        "seg": seg, "progress": progress}}}
        for i in range(n)
    ]


class TestThroughput(unittest.TestCase):
    def test_idle(self):
        self.assertEqual(classify([])["throughput"]["status"], "idle")

    def test_optimal(self):
        r = classify([_vid(speed_ratio=2.5)])
        self.assertEqual(r["throughput"]["status"], "optimal")

    def test_degraded(self):
        r = classify([_vid(speed_ratio=0.6)])
        self.assertEqual(r["throughput"]["status"], "degraded")

    def test_slow(self):
        r = classify([_vid(speed_ratio=0.1)])
        self.assertEqual(r["throughput"]["status"], "slow")

    def test_slowest_video_wins(self):
        r = classify([_vid(video_id="a", speed_ratio=3.0),
                      _vid(video_id="b", speed_ratio=0.1)])
        self.assertEqual(r["throughput"]["status"], "slow")


class TestResources(unittest.TestCase):
    def test_idle_when_nothing_analyzing(self):
        r = classify([{"video_id": "v", "status": "queued"}],
                     {"gpu": {"util_pct": 0, "mem_used_mb": 1, "mem_total_mb": 100}})
        self.assertEqual(r["resources"]["status"], "idle")

    def test_optimal_busy_gpu(self):
        r = classify([_vid()], {"gpu": {"util_pct": 70, "mem_used_mb": 1000,
                                        "mem_total_mb": 10000}})
        self.assertEqual(r["resources"]["status"], "optimal")

    def test_underutilized(self):
        r = classify([_vid()], {"gpu": {"util_pct": 3, "mem_used_mb": 1000,
                                        "mem_total_mb": 10000}})
        self.assertEqual(r["resources"]["status"], "underutilized")

    def test_saturated_vram(self):
        r = classify([_vid()], {"gpu": {"util_pct": 70, "mem_used_mb": 9800,
                                        "mem_total_mb": 10000}})
        self.assertEqual(r["resources"]["status"], "saturated")

    def test_saturated_container_mem(self):
        r = classify([_vid()], {"gpu": {"util_pct": 70, "mem_used_mb": 10,
                                        "mem_total_mb": 10000},
                                "containers": [{"name": "api", "mem_pct": 95}]})
        self.assertEqual(r["resources"]["status"], "saturated")


class TestLiveness(unittest.TestCase):
    def test_error_dominates(self):
        r = classify([_vid(status="error", filename="bad.mp4")])
        self.assertEqual(r["liveness"]["status"], "error")

    def test_idle(self):
        self.assertEqual(classify([])["liveness"]["status"], "idle")

    def test_healthy_when_progress_moves(self):
        # History where the key changes => not flat.
        h = [{"epoch": 1000, "gpu_util": 0,
              "vids": {"v1": {"done": 1, "total": 8, "seg": 1, "progress": 0.1, "speed": 2}}},
             {"epoch": 1100, "gpu_util": 0,
              "vids": {"v1": {"done": 2, "total": 8, "seg": 2, "progress": 0.2, "speed": 2}}}]
        r = classify([_vid()], {"gpu": {"util_pct": 0}}, history=h, now_epoch=1100)
        self.assertEqual(r["liveness"]["status"], "healthy")

    def test_stalled_flat_and_gpu_idle(self):
        h = _hist(40, 1000, gpu=0, step=5)  # ~195s flat, GPU idle
        r = classify([_vid()], {"gpu": {"util_pct": 0}}, history=h, now_epoch=1000 + 39 * 5)
        self.assertEqual(r["liveness"]["status"], "stalled")
        self.assertEqual(r["overall"]["status"], "stalled")

    def test_not_stalled_when_gpu_busy(self):
        h = _hist(40, 1000, gpu=80, step=5)
        r = classify([_vid()], {"gpu": {"util_pct": 80}}, history=h, now_epoch=1000 + 39 * 5)
        self.assertNotEqual(r["liveness"]["status"], "stalled")

    def test_at_risk_between_60_and_120(self):
        h = _hist(20, 1000, gpu=0, step=5)  # ~95s flat
        r = classify([_vid()], {"gpu": {"util_pct": 0}}, history=h, now_epoch=1000 + 19 * 5)
        self.assertEqual(r["liveness"]["status"], "at_risk")


class TestOverall(unittest.TestCase):
    def test_overall_is_worst(self):
        # slow throughput (sev 3) + healthy liveness + saturated would be 3; pick error path.
        r = classify([_vid(status="error")])
        self.assertEqual(r["overall"]["status"], "error")

    def test_severity_ordering(self):
        self.assertLess(severity("optimal"), severity("degraded"))
        self.assertLess(severity("degraded"), severity("stalled"))
        self.assertLess(severity("stalled"), severity("error"))

    def test_overall_carries_cluster_label(self):
        r = classify([_vid(speed_ratio=0.05)])
        self.assertIn("throughput", r["overall"]["description"])


class TestStructure(unittest.TestCase):
    def test_every_cluster_has_keys(self):
        r = classify([_vid()], {"gpu": {"util_pct": 70}})
        for k in ("throughput", "resources", "liveness", "overall"):
            self.assertIn("status", r[k])
            self.assertIn("description", r[k])
            self.assertIn("recommendation", r[k])


if __name__ == "__main__":
    unittest.main(verbosity=2)
