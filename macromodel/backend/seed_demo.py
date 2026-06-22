#!/usr/bin/env python
"""Offline end-to-end demo: build a demo scenario, run the 4-step model, calibrate,
and report the before/after fit. Exercises the full stack (API + DB + UXsim) over HTTP.

Run inside the api container:  python seed_demo.py
"""
from __future__ import annotations

import os
import sys
import time

import httpx

BASE = os.environ.get("MACROMODEL_SELF_URL", "http://localhost:8100")


def _wait_healthy(c: httpx.Client, tries: int = 60) -> None:
    for _ in range(tries):
        try:
            if c.get("/healthz").status_code == 200:
                return
        except Exception:
            pass
        time.sleep(1)
    raise SystemExit(f"macromodel-api not healthy at {BASE}")


def main() -> int:
    with httpx.Client(base_url=BASE, timeout=600.0) as c:
        _wait_healthy(c)

        print("• building demo scenario …")
        sc = c.post("/scenarios/demo").json()
        sid = sc["id"]
        print(f"  scenario {sid[:8]}: {sc['n_nodes']} nodes, {sc['n_links']} links, "
              f"{sc['n_zones']} zones, {sc['n_counters']} counters")

        print("• running 4-step model (uncalibrated) …")
        before = c.post(f"/scenarios/{sid}/run-4step", json={"beta": 0.1}).json()
        print(f"  before:  mean GEH={before['mean_geh']:.2f}  "
              f"GEH<5={before['pct_geh_lt5']:.0f}%  RMSE={before['rmse']:.0f}  VKT={before['total_vkt']:.0f}")

        print("• calibrating (path-based ODME) …")
        cal = c.post(f"/scenarios/{sid}/calibrate", json={}).json()
        a, b = cal["after"], cal["before"]
        print(f"  after:   mean GEH={a['mean_geh']:.2f}  "
              f"GEH<5={a['pct_geh_lt5']:.0f}%  RMSE={a['rmse']:.0f}")

        print(f"\n  convergence: " + "  ".join(f"{h['mean_geh']:.1f}" for h in cal["history"]))
        improved = a["mean_geh"] < b["mean_geh"]
        target = a["pct_geh_lt5"] >= 85.0
        print("\nRESULT:", "PASS ✅" if improved else "FAIL ❌",
              f"(mean GEH {b['mean_geh']:.2f} → {a['mean_geh']:.2f}, "
              f"GEH<5 {b['pct_geh_lt5']:.0f}% → {a['pct_geh_lt5']:.0f}%; "
              f"85% target {'met' if target else 'not met'})")
        return 0 if improved else 1


if __name__ == "__main__":
    sys.exit(main())
