# SCCP Current Plan

Status snapshot: [IN_PROGRESS] on 2026-05-17

## Phase 1 - Architecture

- [DONE] Reconstruct the traffic-counter SCCP document structure around the actual repo pipeline, module boundaries, and top-level dataflows.
  - Notes: Anchored to `README.md`, `api/app/main.py`, `api/app/routers/*.py`, `api/app/services/*.py`, `worker/main.py`, `worker/pipeline.py`, `frontend/streamlit_app.py`, `frontend/sidebar.py`, and `frontend/api_client.py`.
- [DONE] Rebuild the counting-line advanced UI overlay as a distinct hybrid module.
  - Notes: The target UX is a React/Vite viewport embedded into Streamlit, replacing the static image-and-canvas editor in the current Count & Export page.

## Phase 2 - Semantic Contracts

- [DONE] Define workspace-level semantic contract files for the frontend, API, worker, and shared storage boundary.
  - Notes: Contracts mirror the current implementation and describe the real call graph, payload shapes, and storage keys.
- [DONE] Add the counting-line overlay semantic contract, dataflow diagrams, and page skeleton.
  - Notes: This describes the hybrid viewport, live line editing, heatmap overlay, auto-suggest flow, and export handoff.

## Phase 3 - Implementation

- [DONE] Implement the hybrid overlay viewport spec builder in the Streamlit host page.
  - Notes: `frontend/pages/2_Count_and_export_hybrid.py` now normalizes workspace, analyzed videos, and saved lines into a deterministic viewport spec for the future React/Vite overlay.
- [DONE] Scaffold the React/Vite hybrid overlay bundle entrypoint and core viewport state model.
  - Notes: `frontend/hybrid_viewport/` now contains a Vite app shell, typed viewport model, responsive placeholder UI, and build config.
- [DONE] Add the overlay bridge payload serializer and structured host snapshot preview.
  - Notes: `frontend/hybrid_viewport/src/viewportState.ts` now emits a typed overlay snapshot for the future Streamlit bridge.
- [DONE] Emit the overlay snapshot to the host bridge on viewport changes.
  - Notes: `frontend/hybrid_viewport/src/App.tsx` now posts the snapshot to `window.parent` and emits a local custom event for the host shell.
- [DONE] Add a host-aware overlay root that accepts bootstrap messages after mount.
  - Notes: `frontend/hybrid_viewport/src/main.tsx` now listens for `traffic-counter-host-shell` messages and re-seeds the viewport state.
- [DONE] Resync the overlay model when host bootstrap props change.
  - Notes: `frontend/hybrid_viewport/src/App.tsx` now resets local overlay state when the host injects a new viewport spec or line set.
- [DONE] Replace the placeholder viewport stage with an interactive SVG line canvas.
  - Notes: `frontend/hybrid_viewport/src/App.tsx` now renders saved lines, selection state, and overlay layers directly in the viewport.
- [DONE] Verify and document the Streamlit-to-React integration contract.
  - Notes: `artifacts/semantic_contracts/counting_line_overlay/contract.md` now specifies the host bootstrap, overlay snapshot, and bridge rules.
- [DONE] Wire the Streamlit page to the React custom component bridge.
  - Notes: `frontend/pages/2_Count_and_export_hybrid.py` now renders the bridge component and receives overlay snapshots back in Python.
- [DONE] Correct the phase-1 and phase-2 integration contract for the Streamlit component protocol.
  - Notes: The docs now require a valid Streamlit custom component handshake and explicitly call out `Unrecognized component API version: 'undefined'` as a bridge violation.
- [DONE] Fix the bridge handshake to emit an explicit component API version.
  - Notes: `frontend/hybrid_viewport/streamlit_bridge/index.html` now includes `apiVersion: 1` in readiness, frame-height, and component-value messages.
- [DONE] Replace the unsupported component handshake with a Streamlit HTML embed bridge.
  - Notes: `frontend/hybrid_viewport/streamlit_bridge/__init__.py` now uses `components.html(...)` to host the React iframe and relay bootstrap data without the custom-component API error.
- [DONE] Revise the hybrid overlay architecture to require a browser-reachable React guest document instead of a localhost-only default.
  - Notes: The docs now treat the React guest as an inline browser-reachable document, mark localhost as dev-only, and require a degraded state when the guest cannot load.
