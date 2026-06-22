# Module 04 — Counter→link bridge

> **Status:** not started · **Size:** S–M · **Depends on:** #1, the existing `/snap`
> Connects the two repo subsystems: ingests **traffic-counter** directional counts and binds them
> to links (the calibration targets). Index: [`../implementation-plan.md`](../implementation-plan.md) ·
> Contract: [`../data-contract.md`](../data-contract.md).

## Goal

Take a counting line from the `traffic-counter` app (a point + a measured volume + a direction)
and attach it to the correct directed `Link`, populating `Counter.snapped_link_id`,
`link_direction`, and `observed_vph`/`pcu_vph` — the inputs ODME (#5) consumes.

## New code

- `app/routers/counters.py` — `ingest` + `resnap` (the existing `POST …/counters` create stays in
  `network.py`; optionally migrate it here later). Register in `app/main.py`.
- `app/geo.py` — add `bearing_deg(p1, p2)` (used to resolve which directed link the count maps to).
- Schemas in `app/schemas.py`: `CounterIngest`, `CounterRead` (extend existing).

### Endpoints
| Method | Path | Purpose |
|---|---|---|
| `POST` | `/scenarios/{sid}/counters/ingest` | snap a count to nearest link + resolve direction |
| `POST` | `/scenarios/{sid}/counters/{id}/resnap` | re-bind after topology edits (#2) |

**Models touched:** `Counter` (write); `Link` (read, for bearing).

## Data contract (this module)

```jsonc
// POST /scenarios/{sid}/counters/ingest
{ "lon": 69.245, "lat": 41.3115,
  "observed_vph": 820, "pcu_vph": 950, "hours": 1.0,
  "source_video_id": "vid_12", "source_line_id": "line_3",
  "direction_hint_deg": 95 }          // optional bearing of the line's +ve crossing
// -> 201
{ "counter_id": "…", "snapped_link_id": "…", "link_direction": "AB", "distance_m": 12.4 }
```

## Invariants (assert in tests)

- `snapped_link_id` is always set, or **422** if no link within a configurable `max_snap_m`.
- `link_direction ∈ {AB, BA}`, chosen by comparing `direction_hint_deg` to each candidate link's
  bearing (reuse the GiST KNN candidates from `/snap`, not a full scan).
- **Idempotent** on `(source_video_id, source_line_id)` — re-ingest updates the same `Counter`.
- `resnap` after a link split picks the correct **child** link.

## Done when (`tests/test_counter_bridge.py`, real PostGIS)

- ingest on a 2-link (AB/BA) scenario → correct `snapped_link_id` + `link_direction` for a given hint.
- re-ingest same source ids → same row updated (no duplicate).
- after splitting the snapped link (#2), `resnap` re-binds to the nearer child.

## Notes / deps

Reuses the existing `/snap` KNN; the only new logic is direction resolution (bearing compare) and
provenance-keyed upsert. No new external dep.
