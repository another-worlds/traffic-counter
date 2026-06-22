# Module 06 — Tiles + web shell

> **Status:** not started · **Size:** L (incremental) · **Depends on:** #1 (data); #2/#3 to have
> edits/results worth showing. **Sketch-level** — lower confidence; firms up after #1–#3 land.
> Index: [`../implementation-plan.md`](../implementation-plan.md) · Contract: [`../data-contract.md`](../data-contract.md).

## Goal

A Visum-like **web docking UI**: render the network/results from vector tiles and drive edits +
the procedure list through `core-api`. Per the north-star report
(`macromodel/docs/architecture/tooling-landscape.md`): **Martin** (PostGIS→MVT) + **MapLibre GL** +
**deck.gl** overlays + **Dockview** panels.

## New code (sketch)

- **Tiles:** add a `martin` service to `docker-compose.yml` (reads `model-db`), exposing MVT layers
  for `links` (styled by volume/GEH), `nodes`, `zones`, `counters`. Suggested port **:8210**
  (counter 8000s / macromodel 81xx / platform 82xx — keep the lane).
- **Frontend:** new `platform/web/` — React + MapLibre GL JS + deck.gl + **Dockview** panels
  (map · procedure list · object editor · results). Talks to `core-api` (:8200) + martin tiles.
- **First slice:** render the seeded network from tiles; click a link → editor panel → call the
  topology endpoint (#2) → re-render.

## Architecture rules (don't violate)

- **`core-api` stays the single source of truth.** Tiles are **read-through** from PostGIS; all
  edits go through the API (never direct browser→PostGIS writes).
- Tiles serve geometry + a few display attrs; rich attributes/edits come from the JSON API.
- Result styling (volumes/GEH) joins `AssignmentResult` artefacts (#3) — expose a tiles-friendly
  view or an API endpoint the layer can query.

## Done when

- `martin` serves the seeded network; the web shell renders links/nodes/zones.
- One Dockview panel round-trips an edit (select link → split/move → map updates).

## Notes / deps

New JS toolchain + a Martin container. Largest effort here is frontend, not backend; it can land
in thin vertical slices (tiles-only → read-only map → one editable panel → procedure list). Defer
until at least #1 (data) and ideally #2/#3 (something to edit/show) exist.
