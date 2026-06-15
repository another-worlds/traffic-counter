# Implementation plan — next increments (platform/)

> The sequencing **map** for building the macromodel rewrite beyond the data-contract
> foundation. Each module has a full, independently-implementable spec under
> [`modules/`](modules/). This doc holds the *order, dependencies, and decisions*; it does **not**
> duplicate the per-module detail.
>
> **Contract lifecycle:** a module's request/response shapes live in its `modules/NN-*.md`
> spec while it is being built; its **persistent/artefact contracts** are recorded in
> [`data-contract.md`](data-contract.md) under "Planned contract surface". When a module ships,
> its now-stable contract **graduates** into the main body of `data-contract.md` (the canonical
> as-built contract), and its `modules/` spec is marked **done**.

## Where we are

The foundation (`platform/core-api`, commit `33de394`) is a **spatial substrate, not yet a
model**: `Scenario/Node/Link/Zone/Counter` CRUD, `/network` (GeoJSON), `/snap` (GiST KNN), with
geometry helpers in `app/geo.py`. Everything that *computes* (assignment, ODME) or *edits
topology* is dormant — the tables exist in `app/models.py` (classes, demand, matrices, results,
procedures, PuT) but have **no routers/engines** and are empty. Each module below lights up one
dormant slice.

## Backbone (dependency-ordered)

| # | Module | Spec | Depends on | Size | One-line done-when |
|---|---|---|---|---|---|
| 1 | Seed & classes | [`modules/01-seed-and-classes.md`](modules/01-seed-and-classes.md) | — | **S** | `seed-demo` builds a renderable network + fills the class/demand tables |
| 2 | Topology edit | [`modules/02-topology-edit.md`](modules/02-topology-edit.md) | 1 (for test data) | **M–L** | split/merge/move preserve length + GMNS connectivity |
| 3 | Engine materialisation | [`modules/03-engine-materialisation.md`](modules/03-engine-materialisation.md) | 1 | **M–L** | `assign` writes per-link volumes + `AssignmentResult` |
| 4 | Counter→link bridge | [`modules/04-counter-bridge.md`](modules/04-counter-bridge.md) | 1, `/snap` | **S–M** | `counters/ingest` snaps a count to a link + direction |
| 5 | ODME / calibration | [`modules/05-odme-calibration.md`](modules/05-odme-calibration.md) | 3, 4 | **M** | `calibrate` reduces mean GEH vs observed counters |
| 6 | Tiles + web shell | [`modules/06-tiles-web-shell.md`](modules/06-tiles-web-shell.md) | 1 (+2/3 to show) | **L** | tiles render the seeded network; one panel round-trips an edit |

## The one sequencing decision: #2 vs #3

- **Topology-first (recommended):** de-risk the hardest *custom* build (no OSS turnkey, no legacy
  equivalent) while the surface is small. Assignment (#3) is lower-risk integration work
  (AequilibraE/Path4GMNS) that benefits from a stable editing layer underneath.
- **Engine-first:** get a visible assignment loop sooner; do topology editing after.

**Start with #1 regardless** — it is ~half a day, fills the class/demand tables the FKs already
point at, and makes `/network` render something real, de-risking everything after it.

## Cross-cutting (address when it bites, not as gates)

- **Procedure-list executor** — once #3/#5 exist, wrap them as ordered, re-runnable `Procedure`
  rows (the Visum recompute spine). The `procedures` table is already defined.
- **Scenario branching** — implement copy-on-write off the existing `Scenario.parent_id` before
  scenario comparison matters.
- **Alembic** — adopt before the *next* schema change now that these tables get real use
  (foundation deliberately used `create_all`).
- **Async SQLAlchemy** — only if request throughput demands it (sync by design today).

## How to add a module spec (keep them uniform)

Copy the shape of `modules/01-*.md`: **Goal · New code** (files under `app/`, schemas in
`app/schemas.py`, models touched) **· Data contract** (request/response; artefacts → link to
`data-contract.md`) **· Invariants · Done when** (a `tests/test_*.py` run against real PostGIS)
**· Notes/deps**. Add the row here and a pointer from [`../ROADMAP.md`](../ROADMAP.md).
