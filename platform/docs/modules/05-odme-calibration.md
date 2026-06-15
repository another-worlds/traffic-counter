# Module 05 — ODME / calibration

> **Status:** not started · **Size:** M · **Depends on:** #3 (assignment), #4 (counters)
> The macromodel's distinctive capability: bend the demand matrix until assigned volumes match
> observed counts. Index: [`../implementation-plan.md`](../implementation-plan.md) · Contract:
> [`../data-contract.md`](../data-contract.md).

## Goal

Origin-Destination Matrix Estimation: iteratively adjust an OD `Matrix` so that assigned link
volumes match `Counter.observed_vph`, scored by **GEH**. Default method is **select-link
incidence** ODME (engine-agnostic).

## New code

- `app/services/odme.py` — the iteration loop (assign → select-link incidence → scale OD cells → repeat).
- `app/routers/calibration.py` — HTTP layer; register in `app/main.py`.
- Schemas in `app/schemas.py`: `CalibrateRequest`, `CalibrationRunRead`.
- Reuse `app/services/assignment.py` (#3) and `app/services/omx_io.py`.

### Endpoints
| Method | Path | Purpose |
|---|---|---|
| `POST` | `/scenarios/{sid}/calibrate` | run ODME against counters; write a `CalibrationRun` + calibrated `Matrix` |
| `GET` | `/scenarios/{sid}/calibration-runs[/{id}]` | list/fetch runs (incl. `history`) |

**Models touched:** `CalibrationRun, Matrix` (write); `Counter, AssignmentResult, Link` (read).

## Data contract (this module)

```jsonc
// POST /scenarios/{sid}/calibrate
{ "matrix_id": "…", "method": "select_link_odme",
  "max_iter": 20, "geh_target": 5.0, "converge_pct": 0.85 }
// -> 201
{ "calibration_run_id": "…", "calibrated_matrix_id": "…",
  "mean_geh": 3.9, "pct_geh_lt5": 0.88, "iterations": 11, "converged": true }
```

`CalibrationRun.history` (JSON): per-iteration `[{ "iter": n, "mean_geh": …, "pct_geh_lt5": … }]`.
GEH per count = `sqrt( 2*(M-C)^2 / (M+C) )` with `M`=modelled, `C`=`observed_vph`.

## Invariants (assert in tests)

- `mean_geh` is **non-increasing** across accepted iterations (revert a step that worsens it).
- Converged ⟺ `pct_geh_lt5 ≥ converge_pct` (or `max_iter` hit).
- The calibrated matrix is **non-negative**; a new `Matrix` row with `step="calibrated"` is written
  (the seed matrix is preserved, not overwritten).

## Done when (`tests/test_odme.py`, real PostGIS)

- Seed a scenario whose assigned volumes deliberately mismatch a few counters → `calibrate` →
  `mean_geh` drops and/or `converged=true`; `history` is monotone.

## Notes / deps

No new external dep beyond #3's. The select-link incidence can come from the engine (AequilibraE
supports select-link analysis); keep the scaling step engine-agnostic so the oracle path still works.
