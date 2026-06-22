# Module 01 — Seed & classes

> **Status:** not started · **Size:** S · **Depends on:** — (pure DB + existing helpers)
> Independently implementable. Index: [`../implementation-plan.md`](../implementation-plan.md) ·
> Foundation contract: [`../data-contract.md`](../data-contract.md).

## Goal

Populate the empty **class** tables (`LinkType/NodeType/ZoneType`, already FK'd from
links/nodes/zones) and provide a one-call **demo scenario**, so every later module has real data
+ valid FK targets and `/network` renders something. Cheapest, highest-leverage first step.

## New code

- `app/routers/classes.py` — CRUD for the three type tables.
- `app/seed.py` — `seed_demo(db, name, preset)` builder.
- Register both in `app/main.py` (`app.include_router(...)`).
- Schemas in `app/schemas.py`: `LinkTypeCreate/Read`, `NodeTypeCreate/Read`, `ZoneTypeCreate/Read`, `SeedResult`.

### Endpoints
| Method | Path | Purpose |
|---|---|---|
| `POST/GET/DELETE` | `/scenarios/{sid}/link-types[/{id}]` | LinkType CRUD |
| `POST/GET/DELETE` | `/scenarios/{sid}/node-types[/{id}]` | NodeType CRUD |
| `POST/GET/DELETE` | `/scenarios/{sid}/zone-types[/{id}]` | ZoneType CRUD |
| `POST` | `/scenarios/{sid}/seed-demo?preset=grid3x3` | build nodes/links/zones/classes/demand into `{sid}` |

**Models touched:** `LinkType, NodeType, ZoneType, Node, Link, Zone, Connector, Mode, Activity, DemandLayer, ZoneDemand`.

## Data contract (this module)

```jsonc
// POST /scenarios/{sid}/link-types
{ "name": "arterial", "rank": 2, "num_lanes": 2, "capacity_vph": 1800, "free_speed_kmh": 60 }

// POST /scenarios/{sid}/seed-demo?preset=grid3x3   -> 201
{ "scenario_id": "…", "nodes": 9, "links": 24, "zones": 4,
  "link_types": 3, "modes": 2, "activities": 3, "demand_layers": 4 }
```

`preset=grid3x3`: a 3×3 node grid (~300 m spacing near a configurable origin lon/lat), bidirectional
links (two directed `Link` rows each), 4 corner `Zone`s with centroids + connector nodes, default
classes, `Mode` {PrT,PuT}, `Activity` {H,W,O}, `DemandLayer` {HW,WH,HO,OH}, and `ZoneDemand` rows.

## Invariants

- Link geometry/`length_m` via `geo.straight_line` + `geo.line_length_m` (don't hand-compute).
- Every seeded link sets `link_type_id`; nodes `node_type_id`; zones `zone_type_id` + `centroid`.
- Re-running `seed-demo` on a scenario that already has a demo is a no-op or replaces cleanly
  (idempotent — pick one and test it).

## Done when (`tests/test_seed.py`, real PostGIS)

- `seed-demo` on a fresh scenario → `/network` returns the expected node+link+zone feature counts.
- `GET …/link-types` non-empty; every `Link.link_type_id` resolves to a row.
- Class-table CRUD round-trips (create→get→delete).

## Notes / deps

No new external deps. This is the reference module for spec shape — keep the others uniform with it.
