# Module 02 — Topology edit service

> **Status:** not started · **Size:** M–L · **Depends on:** #1 (for test data)
> The **largest build gap** — no OSS turnkey, no legacy equivalent; correctness is the hard part.
> Index: [`../implementation-plan.md`](../implementation-plan.md) · Contract: [`../data-contract.md`](../data-contract.md).

## Goal

In-place graph editing that keeps the network **GMNS-consistent**: splitting/merging links,
moving/splitting nodes, with re-noding, length recompute, directed-pair handling, and counter
re-snap. This is the editor's core and the rewrite's distinctive capability.

## New code

- `app/services/topology.py` — pure functions operating on ORM objects (the logic; unit-testable).
- `app/routers/topology.py` — thin HTTP layer; register in `app/main.py`.
- Schemas in `app/schemas.py`: `SplitLinkRequest`, `MergeLinksRequest`, `MoveNodeRequest`, `TopologyResult`.

### Endpoints
| Method | Path | Body | Effect |
|---|---|---|---|
| `POST` | `/scenarios/{sid}/links/{id}/split` | `{ "at": {"lon","lat"} }` *or* `{ "fraction": 0..1 }` | insert `Node` on the line, replace `Link` with two; rewire `from/to`; recompute `length_m` |
| `POST` | `/scenarios/{sid}/links/merge` | `{ "link_ids": ["a","b"] }` | collapse two links sharing a degree-2 node into one; drop the middle node |
| `POST` | `/scenarios/{sid}/nodes/{id}/move` | `{ "lon","lat" }` | move node; update geom of all incident links; recompute lengths; re-snap affected counters |
| `POST` | `/scenarios/{sid}/nodes/{id}/split` | `{ "keep_links": [...] }` | split one node into two (untangle); reassign incident links *(lower priority — sketch)* |

**Models touched:** `Node, Link, Counter` (re-snap).

## Data contract (this module)

```jsonc
// POST /scenarios/{sid}/links/{id}/split
{ "at": { "lon": 69.245, "lat": 41.3115 } }     // projected to nearest point on the line
// -> 201
{ "new_node_id": "…", "link_ids": ["…AB1", "…AB2"], "removed_link_ids": ["…AB"] }

// POST /scenarios/{sid}/nodes/{id}/move
{ "lon": 69.246, "lat": 41.312 }
// -> { "node_id": "…", "updated_link_ids": ["…"], "resnapped_counter_ids": ["…"] }
```

## Invariants (assert in tests)

- **Connectivity:** after any op, every `Link.from_node_id`/`to_node_id` references an existing `Node`.
- **Length conservation:** split → Σ child `length_m` == parent (± ε); merge → sum of parts.
- **On-geometry:** the split point is the nearest point *on* the LineString (project via shapely);
  recompute lengths with `geo.line_length_m`, never trust the request distance.
- **Directed pairs:** if a reverse `Link` exists between the same node pair, apply the op symmetrically.
- **Spatial index:** geom-column updates keep the GiST index valid (it does automatically — just
  write through the ORM, don't bypass with raw SQL that skips triggers).

## Done when (`tests/test_topology.py`, real PostGIS)

- split preserves total length + connectivity; the new node lies on the original geometry.
- merge **inverts** a split (round-trip → original topology + length).
- move updates every incident link's geometry + `length_m`; a counter on an affected link re-snaps.

## Notes / deps

No new external dep — `shapely` (already used by `geo.py`) does projection/splitting. Budget the
time on **correctness + edge cases** (self-loops, degree-1 nodes, split at an existing vertex).
