# platform/ — roadmap & status

> Living state of the macromodel rewrite. **Why** decisions were made → `macromodel/docs/adr/`.
> **Target** architecture → `macromodel/docs/architecture/tooling-landscape.md`.
> **Contract** → `platform/docs/data-contract.md`. **How to build next** →
> `platform/docs/implementation-plan.md` (+ per-module specs in `docs/modules/`). Updated: 2026-06-15.

## Done

- **Data-contract foundation** — `platform/core-api` (commit `33de394`).
  - Native PostGIS geometry (GeoAlchemy2, SRID 4326) + **GiST** indexes — the break from the
    legacy GeoJSON-in-JSON. Typed GeoJSON contract; scenario + network CRUD; GeoJSON `/network`
    export; **GiST-assisted KNN `/snap`**. GMNS-aligned fields; matrices reference out-of-row OMX.
  - New compose services: `model-db` (PostGIS, :5434), `core-api` (:8200).
  - **Verified vs real PostGIS:** 4/4 contract tests (geometry round-trip, geodesic length,
    `/network` FeatureCollection, KNN snap, GiST index present) + containerized smoke
    (`/healthz` + create→snap). Image builds (in-container `pip` needs the proxy CA in the web sandbox).

## Next (prioritized)

> Implementable specs → [`docs/implementation-plan.md`](docs/implementation-plan.md) (sequencing +
> the #2↔#3 decision) and [`docs/modules/`](docs/modules/) (one full spec per module). Ordering
> below matches the module numbering; seed pulled to #1 as the cheap enabler.

1. **Seed & classes** — fill the class/demand tables + a one-call demo network so everything
   downstream has data + FK targets. → [`docs/modules/01-seed-and-classes.md`](docs/modules/01-seed-and-classes.md)
2. **Topology edit service** — split/merge link, snap/move node, GMNS-consistent re-noding +
   length recompute. The largest "build" gap. → [`docs/modules/02-topology-edit.md`](docs/modules/02-topology-edit.md)
3. **Engine materialisation** — PostGIS → GMNS/OMX → AequilibraE assignment; per-link volumes →
   `AssignmentResult` (OMX, out-of-row). → [`docs/modules/03-engine-materialisation.md`](docs/modules/03-engine-materialisation.md)
4. **Counter→link bridge** — ingest `traffic-counter` counts and snap to links (builds on
   `/snap`) → `counters.observed_vph`. → [`docs/modules/04-counter-bridge.md`](docs/modules/04-counter-bridge.md)
5. **ODME / calibration** — select-link-incidence ODME against counters → `CalibrationRun`.
   → [`docs/modules/05-odme-calibration.md`](docs/modules/05-odme-calibration.md)
6. **Tiles + web shell** — Martin (PostGIS→MVT) + React/MapLibre/deck.gl + Dockview.
   → [`docs/modules/06-tiles-web-shell.md`](docs/modules/06-tiles-web-shell.md)

## Deferred / open decisions

- **Async SQLAlchemy** under psycopg3 — the report flags GeoAlchemy2 async as under-documented;
  the foundation deliberately uses proven **sync** sessions. Spike before adopting.
- **Alembic** migrations — greenfield uses `create_all`; adopt Alembic before schema churn.
- Demand/procedure tables are defined in `models.py` (part of the canonical contract) but have
  no routers/engines yet — they land with the modules above (seed, engine, ODME).
