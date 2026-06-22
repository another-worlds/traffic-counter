# Module 03 — Engine materialisation (assignment)

> **Status:** not started · **Size:** M–L · **Depends on:** #1
> The **headline** capability: turn (network + demand) into per-link volumes — the reason this is
> a *macro**model***. Index: [`../implementation-plan.md`](../implementation-plan.md) · Contract:
> [`../data-contract.md`](../data-contract.md) (artefact formats live there).

## Goal

Materialise the native-PostGIS network into the GMNS/OMX interchange, run a static traffic
assignment, and persist per-link volumes + an `AssignmentResult`. Use **AequilibraE** as the
engine and **Path4GMNS** as a reference oracle for tests.

## New code

- `app/services/materialise.py` — PostGIS → GMNS `node`/`link` DataFrames (GMNS field names already
  match `models.py`: `from_node_id`, `to_node_id`, `length_m`, `lanes`, `free_speed_kmh`, `capacity_vph`).
- `app/services/omx_io.py` — read/write OMX (HDF5) demand/skim matrices; populates `Matrix.storage_ref`.
- `app/services/assignment.py` — engine wrapper (build graph → load matrix → assign → volumes).
- `app/routers/assignment.py` — HTTP layer; register in `app/main.py`.
- Schemas in `app/schemas.py`: `AssignRequest`, `AssignmentResultRead`.
- `requirements.txt`: add `aequilibrae`, `openmatrix` *(heavier build — see Notes)*.

### Endpoints
| Method | Path | Purpose |
|---|---|---|
| `POST` | `/scenarios/{sid}/assign` | build GMNS, assign, write volumes + `AssignmentResult` |
| `GET` | `/scenarios/{sid}/assignment-results[/{id}]` | list/fetch results |
| `GET` | `/scenarios/{sid}/network?with=volumes&result_id=…` | `/network` features annotated with `volume_vph` *(optional)* |

**Models touched:** `Matrix, AssignmentResult` (write); `Link, Zone, Connector, ZoneDemand, DemandLayer` (read).

## Data contract (this module)

```jsonc
// POST /scenarios/{sid}/assign
{ "matrix_id": "…",            // optional: omit -> derive from ZoneDemand via gravity (DemandLayer.beta)
  "algorithm": "bfw",          // msa | frank-wolfe | bfw
  "max_iter": 50, "rgap": 1e-4 }
// -> 201
{ "assignment_result_id": "…", "storage_ref": "…", "total_vkt": 12345.6,
  "iterations": 23, "converged": true }
```

**Artefacts** (canonical schemas in [`../data-contract.md`](../data-contract.md) → "Artefact formats"):
the OMX demand matrix (`Matrix.storage_ref`) and the per-link volume table (`AssignmentResult.storage_ref`).

## Invariants (assert in tests)

- **Conservation:** Σ assigned origin trips == matrix row totals (± tol).
- **Non-negative** link volumes; **deterministic** given a fixed seed/algorithm.
- Materialisation is **lossless** for GMNS fields (a node/link round-trips PostGIS → GMNS → back).

## Done when (`tests/test_assignment.py`, real PostGIS)

- Assign a `seed-demo` scenario → an `AssignmentResult` row with positive `total_vkt`; volumes
  non-negative; conservation holds.
- (Stretch) on a tiny hand-checkable network, volumes match a **Path4GMNS** oracle within tol.

## Notes / deps

`aequilibrae`/`openmatrix` pull native libs (HDF5, spatialite/GDAL). They **inflate the image
build** and worsen the web-sandbox proxy-CA issue — verify the host-venv install path in
`AGENTS.md` still resolves them, and consider pinning wheels. Keep the engine wrapper swappable
(interface around "graph + matrix → volumes") so Path4GMNS can stand in.
