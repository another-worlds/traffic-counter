# ADR 0002 — Clean-slate rewrite of macromodel onto the target architecture

**Status:** Accepted
**Date:** 2026-06-14
**Scope:** `macromodel` subsystem — the rebuild lives in `/platform`
**Builds on:** [`0001-client-engine-and-data-architecture.md`](0001-client-engine-and-data-architecture.md) + [`../architecture/tooling-landscape.md`](../architecture/tooling-landscape.md) (the north-star report)
**Relationship to legacy:** the existing `macromodel/` app is **frozen, not modified**; it runs in parallel until the rewrite reaches parity.

---

## 1. Context

ADR-0001 and the tooling-landscape report defined a *target* architecture (native-PostGIS
datastore, an AequilibraE-lane engine with GMNS/OMX interchange, Martin/MapLibre rendering, a
web docking UI). The as-built MVP, by contrast, stores geometry as **GeoJSON inside JSON
columns** and does spatial work in shapely/networkx.

That representation is the binding constraint: a JSON column **cannot carry a GiST index**, so
the load-bearing spatial capabilities of the target — KNN snapping (`geom <-> point`),
`ST_DWithin` filtering, and in-DB `ST_AsMVT` vector tiling — are simply not expressible against
it. The question for this phase: **incrementally migrate the existing stack, or rebuild?**

## 2. Decision

- **Rebuild clean-slate, in new containers, starting from the data-contract foundation.**
  Do **not** integrate with, or migrate from, the running legacy stack.
- The rewrite lives in **`/platform`** (named to avoid confusion with the frozen `macromodel/`;
  it *is* the macromodel rebuild).
- The legacy `macromodel/` app keeps running **untouched** until the rewrite reaches feature
  parity; only then is it retired. (See §7.)
- Foundation choices: **native PostGIS geometry** (GeoAlchemy2, SRID 4326) + GiST; a **typed
  GeoJSON** data contract at the API boundary; **GMNS**-aligned network fields; **OMX** for
  out-of-row matrices. Proven **sync** SQLAlchemy; `create_all` now, Alembic before schema churn.

## 3. Why rewrite, not migrate

- The load-bearing capabilities (snap, spatial filters, tiles) all require native geometry +
  GiST. The *first step of any migration* is therefore to re-lay the datastore on native
  geometry — i.e. exactly the rewrite's foundation. Migrating buys little over rebuilding.
- Greenfield avoids carrying GeoJSON-in-JSON assumptions and the UXsim-coupled data model
  forward, and lets us adopt GMNS/OMX interchange from day one.
- **Parallel-until-parity** keeps the working app available and removes big-bang cutover risk.

## 4. What was built (first increment)

`platform/core-api` — FastAPI + SQLAlchemy 2 + GeoAlchemy2 exposing the canonical schema with
native geometry + GiST, scenario/network CRUD, a GeoJSON `/network` export, and a GiST-assisted
KNN `/snap`. New compose services `model-db` (:5434) and `core-api` (:8200). Full contract in
[`../../../platform/docs/data-contract.md`](../../../platform/docs/data-contract.md).

## 5. Verification

Against **real PostGIS**: 4/4 contract tests pass (geometry round-trip, geodesic length,
`/network` FeatureCollection, KNN snap, GiST index present); containerized smoke test green
(`/healthz` + create→snap round-trip). The image builds and runs — with the caveat that an
in-container `pip install` needs the egress-proxy CA in the Claude-Code-on-the-web sandbox.

## 6. Consequences

- **+** Native geometry unblocks snapping, spatial filtering, and vector tiles.
- **+** GMNS/OMX interchange (bridges to/from Visum/EMME and AequilibraE) from the start.
- **−** Two stacks coexist until parity → maintenance overlap. Bounded by freezing the legacy app.
- **−** Features that already exist in legacy (the procedure list, demand model, ODME) must be
  rebuilt. Tracked in [`../../../platform/ROADMAP.md`](../../../platform/ROADMAP.md).
- Immediate follow-ups: topology edit service, AequilibraE materialisation, seed/demo data.

## 7. Status of the legacy `macromodel/` app

Frozen. No new features; bugfixes only if explicitly requested. It remains the source of
behavioural truth (procedures, demand model, calibration) to port against, and is retired once
`/platform` reaches parity.
