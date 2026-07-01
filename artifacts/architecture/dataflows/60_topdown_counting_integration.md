# Dataflow / Integration: Top-Down (Aerial) Video Counting → Macromodel

**Status:** Planning / Draft  
**Date:** 2026-06-26  
**Goal:** Extend the existing traffic-counter ↔ macromodel integration to first-class support **top-down / nadir / drone video sources**.

## 1. Problem & Motivation

Current integration assumes **perspective** (side/front/oblique CCTV) video:

- User draws counting **lines** in arbitrary pixel space.
- Direction is relative ("positive" vs "negative") to the arbitrary line orientation.
- Georeferencing in macromodel is manual: click an approximate point on the map + choose AB/BA mapping.
- No knowledge of camera viewpoint; all spatial work happens *after* counts are produced.

**Top-down video** (drone, mast-mounted nadir, stabilized aerial) changes the value proposition:

- The video frame is nearly orthographic → much closer to the network map view used in macromodel.
- Vehicle headings and lane assignments are more directly observable.
- A single top-down clip can cover an entire intersection or link with multiple movements.
- Accurate **image → world** mapping (homography) becomes feasible with a few ground control points (GCPs) or telemetry.
- Result: higher-confidence link attribution, reduced manual snap error, potential for richer detectors (zones instead of only lines).

Without explicit support, users treat top-down videos the same as perspective ones → lost accuracy and worse UX.

## 2. Current Integration (as of 2026-06)

One-way REST contract only (no shared DB):

```
traffic-counter (api:8000)
  ├── Video (pixel dims, duration, status)
  ├── CountingLine {points: {a,b} in pixels, name, color}
  └── POST /videos/{id}/counts → { per_line: [{line_id, total, by_class, by_direction: {positive,negative}}] }

macromodel (macromodel-api:8100)
  ├── counter_client.py (mirrors traffic-counter api_client)
  ├── GET /counter-sources  (proxy: projects + videos + lines)
  ├── POST /scenarios/{sid}/counters {lat,lon, source_video_id, source_line_id, link_direction}
  ├── POST .../pull-observations  (fetches video + counts, converts duration → vph + PCU)
  └── georef.py (point snap + direction_key + pcu_factor)
```

Key files:
- `api/app/models.py`: Video, CountingLine (only pixel JSON)
- `api/app/schemas.py`: VideoOut, LineOut, CountResponse
- `macromodel/backend/app/models.py`: Counter (source_* ids + geom + snapped_link)
- `macromodel/backend/app/routers/counters.py`: the bridge
- `macromodel/backend/app/services/georef.py`
- `macromodel/frontend/sections/network.py`: DetectorPanel + grab/drop flow
- `frontend/hybrid_viewport/...`: current line editing (kind: line|polyline)

Counting math (`api/app/services/counting.py`) is pure 2D image-plane segment crossing. It is **view-agnostic** and can stay that way.

## 3. Scope of "Top-Down Support"

### In scope (MVP for this integration)
- Tag videos with `view_type`.
- Store lightweight geo-registration (GCPs or homography) per top-down video.
- Surface view_type + registration status through existing proxy and video endpoints.
- Improve macromodel georef + counter creation when registration exists (better initial placement, validation, or direct projection of line geometry).
- UX indicators and differentiated flows in both UIs.
- Keep backward compatibility (all existing perspective flows unchanged).

### Out of scope (later)
- Full orthorectification pipeline in the worker.
- Native support for zone/polygon detectors (can be added later; lines still useful on top-down).
- Automatic lane-level attribution or heading-based classification.
- Drone telemetry (EXIF/GPS track) ingestion.
- Different YOLO post-processing or model for nadir imagery.

## 4. Proposed Data Model Extensions

### traffic-counter (api DB)

**Video** (add columns):
```python
view_type = Column(String(16), default="perspective", nullable=False)
# "perspective" | "topdown" | "oblique" (future)
registration = Column(JSON, nullable=True)
# Example for topdown:
# {
#   "method": "homography" | "affine" | "gcp",
#   "gcps": [  # at least 3-4
#     {"px": [x,y], "world": [lon, lat]}
#   ],
#   "homography": [[h00,h01,h02], ...],   # 3x3, optional precomputed
#   "crs": "EPSG:4326",
#   "bounds_world": [[lon,lat], ...],     # optional
#   "updated_at": "..."
# }
```

**CountingLine** — no structural change initially (still pixel points).  
Later we may add `detector_kind` ("line_crossing", "zone", ...).

Add to `VideoOut` (and internal responses used by proxy):
- `view_type: str`
- `registration: Optional[dict] = None`

No change to track parquet schema (still image cx,cy).

### macromodel (its own DB)

**Counter** (existing):
- Keep `source_video_id`, `source_line_id`.
- Optionally cache `source_view_type` (or always fetch fresh) and `projected_geometry` (GeoJSON of the line or zone in world coords) for top-down sourced counters.

Add a small table or JSON if richer per-counter registration overrides are needed.

## 5. API & Contract Changes

### traffic-counter API (additive)

1. **Video metadata**
   - `PATCH /videos/{id}` (or dedicated) to set `view_type` and `registration`.
   - GET responses include the new fields (already returned via `from_attributes`).
   - Validation: if `view_type == "topdown"` require ≥3 GCPs or a homography matrix on registration.

2. **New or extended endpoints** (minimal):
   - `POST /videos/{video_id}/registration`
     - Body: `{ "view_type": "...", "gcps": [...], "compute_homography": true }`
     - Response: full registration (server can compute + store homography).
   - `DELETE /videos/{video_id}/registration`
   - Optionally `GET /videos/{video_id}/project?points=[...]` — project pixel points → world using registration (utility, useful for UI).

3. **/counter-sources** (in macromodel proxy, but driven by traffic-counter responses)
   - Include per-video: `view_type`, `has_registration`, maybe a summary `registration_method`.
   - Optionally embed lightweight line pixel geometry (already does for lines list).

4. **Counts** — unchanged contract. Direction remains relative to the drawn line's orientation. Top-down users will typically draw lines perpendicular to flow in the correct road azimuth.

### macromodel API extensions

- `GET /counter-sources` (already proxies) will automatically carry the new fields once traffic-counter returns them.
- Enhance `POST /scenarios/{sid}/counters`:
  - Accept optional `use_video_geometry: bool` or registration-driven placement.
  - When source is top-down + registered, compute a suggested `geom` and even a `projected_line_geom` (LineString in world).
- New helper: `POST /scenarios/{sid}/counters/{cid}/project-line` or during creation.
- `georef.py` grows:
  - `project_points(px_points, registration) → world_points`
  - `compute_homography(gcps) → matrix`
  - `snap_or_project(...)` that prefers video-derived geometry when available.
- `counter_video_info` can surface registration status + a "projected location" badge.

The **core counts → vph → PCU** logic in `counts_to_targets` + duration remains identical.

## 6. Georeferencing Flow — Before vs After

**Current (any view):**
1. Draw line in video pixels (traffic-counter).
2. In macromodel map: click near the real-world location of the road segment.
3. Choose link direction.
4. Pull observations (uses duration only).

**Top-down + registration (new path):**
1. Mark the video as `topdown` + supply 3–4 GCPs (click recognizable features in video + click or type lat/lon on map, or paste).
2. (Optional) Compute + persist homography.
3. When creating a counter from that video's line:
   - Offer "Place using video geometry".
   - Project the two endpoints (or midpoint + perpendicular) into world coordinates.
   - Place marker + optionally draw the actual line segment on the map.
   - Still allow manual override + link snap.
4. Pull observations as before.
5. Bonus: store the projected geometry on the Counter for viz and future validation ("does the observed count lie on this link?").

This dramatically reduces placement error for drone footage of intersections.

## 7. UI / UX Changes

### traffic-counter (Count & Export hybrid page)
- In video selector or metadata panel: "View type" select (default Perspective).
- When topdown selected: show "Geo registration" section.
  - Table or interactive picker for GCPs (use existing frame + perhaps a small map widget or manual lon/lat inputs).
  - Button "Compute & save homography".
  - Status badge: "Registered (4 GCPs)" or "Unregistered top-down".
- The line drawing UI itself stays the same (lines work great); perhaps add a "North arrow / scale" hint when registered.
- In viewport bootstrap, pass `viewType` and `registration` so React layer can show different overlays later (e.g. geo grid).

### macromodel (Solara map + Detector tool)
- In DetectorPanel / video list: badge `🛩️ Top-down` or `📷 Perspective` next to each video filename.
- When a top-down video with registration is chosen:
  - Show "Registered" indicator + "Use video geometry for placement" toggle.
  - On grab line → drop: compute suggested location from projected line and auto-snap or pre-fill the drop point.
- In counter list / results layers: for top-down sourced counters, render an extra "video line" overlay (projected) when available.
- Video-info popup (already rich): show registration status + "Open in top-down viewer" hint.

State additions (macromodel/frontend/state.py etc.) are small.

## 8. Implementation Phases (recommended)

**Phase 0 – Preparation (no user-visible change)**
- Add `view_type` + `registration` columns (alembic-style or raw ALTER since current is `create_all`).
- Update SQLAlchemy models + Pydantic schemas (VideoOut).
- Update `counter_client.py` if it needs typed access (currently dicts).
- Default everything to "perspective". Backfill script optional.

**Phase 1 – Metadata & Discovery**
- API endpoints for registration (POST/GET).
- Expose `view_type` + `has_registration` in all video lists and `/counter-sources`.
- UI badges in both apps.
- Update docs + SPEC.md + tests.
- Ensure `counters_fc` and video-info carry the info.

**Phase 2 – Georeferencing math + bridge**
- Implement homography + projection helpers in `macromodel/backend/app/services/georef.py` (pure Python + numpy/shapely).
- Extend counter creation + pull flow to accept/use registration.
- Add projected geometry to Counter model (optional JSON column).
- Unit tests: GCP roundtrip, homography stability, pcu unchanged.

**Phase 3 – UX polish**
- GCP entry UI in traffic-counter (can be simple form + "use current frame coords").
- "Project from video" action in macromodel detector.
- Visual feedback on the ipyleaflet map (draw projected detector lines for top-down counters).
- Validation warnings (e.g., "line projects far from snapped link").

**Phase 4 – Future (not now)**
- Zone detector primitives.
- Worker hints for top-down (different tracker params, heading binning).
- Automatic GCP suggestion from map features.

## 9. Risks & Mitigations

- **Homography instability** with poor GCP choice or non-planar scene → warn user, require min 4 points, show reprojection error.
- **Model performance on top-down** — YOLOv8m (perspective trained) may have lower recall/precision on nadir views. Document that users may need `yolov8m.pt` fine-tune or note accuracy trade-off. No change to pipeline required for MVP.
- **Direction semantics** — Users must still draw the line consistently (A side vs B side determines positive). Top-down makes azimuth obvious; we can later compute "azimuth of line" for suggestions.
- **Multiple views of same location** — A scenario may mix perspective + top-down counters on same links. Calibration already handles heterogeneous observations.
- **Contract drift** — Because coupling is only REST + documented shapes in counter_client, document the new optional fields explicitly.

## 10. Testing & Acceptance

- Existing perspective flows 100% unchanged (all tests green).
- New tests:
  - Registration round-trip + projection math.
  - Counter creation with `use_video_geometry` on a mocked top-down source yields sensible snapped link.
  - GEH calibration still works when some counters come from registered top-down lines.
- Manual: load a (synthetic or real) top-down clip, register with 4 GCPs, pull counts into a small scenario, verify placement accuracy vs manual.
- Demo scenario extension: add one top-down "detector".

## 11. Files Likely to Change (initial survey)

**traffic-counter:**
- `api/app/models.py`
- `api/app/schemas.py`
- `api/app/routers/videos.py` (and/or a new `registration.py`)
- `frontend/pages/2_Count_and_export.py` + hybrid viewport bootstrap
- `frontend/hybrid_viewport/src/...` (pass-through viewType)
- Possibly `api/app/services/suggest.py` (future)

**macromodel:**
- `macromodel/backend/app/models.py` (Counter optional fields)
- `macromodel/backend/app/schemas.py`
- `macromodel/backend/app/routers/counters.py`
- `macromodel/backend/app/services/georef.py` (main new logic)
- `macromodel/backend/app/helpers.py` (counters_fc)
- `macromodel/backend/app/counter_client.py` (minor)
- `macromodel/frontend/sections/network.py` (DetectorPanel + map interactions)
- `macromodel/frontend/state.py`, `api_client.py`
- `macromodel/SPEC.md`
- Tests under `macromodel/backend/tests/`

**Docs:**
- `artifacts/...` (this file)
- `macromodel/README.md` / root README mentions
- New or updated semantic contract if we formalize registration.

## 12. Open Questions (for discussion)

1. Do we want to support **polyline** or **multi-segment** detectors specifically for top-down complex intersections in phase 1?
2. Should registration live only on the Video, or allow per-Counter overrides (rare)?
3. Preferred GCP entry UX in the Streamlit/hybrid page — pure numeric, or click-on-frame + click-on-small-map?
4. Naming: `view_type` vs `camera_view` vs `perspective_type`? `topdown` vs `nadir` vs `aerial`?
5. Do we expose a "projected counts on network" preview before creating the macromodel Counter?
6. Timeline priority vs other work (timestamp correction, more export formats, etc.)?

---

**Next step recommendation:** Review this doc, decide on Level 1 vs Level 2 scope, then implement Phase 0 + Phase 1 (metadata only) as a small safe increment. The georef math can follow quickly once the flags flow through the contract.

This keeps the existing "Detect once, count instantly, calibrate in macromodel" value while unlocking higher-quality inputs from modern drone surveys.