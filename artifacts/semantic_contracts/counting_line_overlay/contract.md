# Counting Line Overlay Semantic Contract

Status: [DONE]

## Scope

The counting-line overlay is the advanced hybrid viewport that replaces the static image-and-canvas editor in the Count & Export workflow.

## Responsibilities

- Render a synchronized 100-frame scrubber over a selected analyzed-video set.
- Maintain a persistent overlay of all saved counting lines, with color-coded layers and visibility toggles.
- Support add, move, edit, delete, and confirm-save interactions for straight or polyline counting lines.
- Show live counts, total crossings, percentage-of-total metrics, auto-suggested lines, heatmap overlay, and direction rose diagnostics.
- Hand persisted changes back to the API and trigger recount/export flows through the existing backend services.

## Inputs

- Selected workspace id.
- Selected analyzed video ids.
- Saved line definitions from the API.
- Frame and trajectory asset URLs from the API.
- Optional heatmap and track-statistics payloads.

## Outputs

- Updated line geometries persisted via the API.
- Local overlay state for active layers, scrub position, and editing mode.
- Recount requests and export requests routed to the API.

## Streamlit-To-React Integration Contract

The Streamlit host page is the orchestration shell. The React/Vite overlay is the interactive viewport. Their communication is explicit and bidirectional, and it goes through a Streamlit custom component bridge:

- Host -> React: bootstrap payload is passed as component arguments from Python.
- React -> Host: overlay snapshot is returned through `Streamlit.setComponentValue(...)` as JSON.

The bridge must implement the Streamlit component handshake correctly (`Streamlit.setComponentReady()` + render event flow, or the equivalent React wrapper from `streamlit-component-lib`). A runtime error such as `Unrecognized component API version: 'undefined'` is a contract violation and means the handshake is still broken.
In development, a localhost frontend URL is acceptable; in production, the component must load a built static frontend bundle.

### Visualization Build Contract

- The component build entry (`dist/index.html`) must reference JS/CSS assets with relative URLs (`./assets/...`) for embedded Streamlit routes.
- Vite must be configured with `base: './'` for production component bundles.
- A module load failure with MIME mismatch (`text/html` served for `index-*.js`) is treated as a hard contract failure because viewport rendering cannot start.
- Browser warnings about unsupported iframe features are non-blocking unless accompanied by handshake or render failure.

### Host Bootstrap Shape

```ts
type HostViewportBootstrap = {
	spec?: {
		projectId?: string;
		videoIds?: string[];
		selectedLineIds?: string[];
		frameCount?: number;
		activeLayers?: string[];
	};
	initialLines?: Array<{
		id: string;
		name: string;
		color: string;
		kind: 'line' | 'polyline';
		points: Array<[number, number]>;
		locked?: boolean;
	}>;
};
```

### Overlay Snapshot Shape

```ts
type BridgePayload = {
	kind: 'overlay-snapshot';
	projectId: string;
	videoIds: string[];
	selectedLineId: string | null;
	currentFrame: number;
	activeLayers: string[];
	lines: Array<{
		id: string;
		name: string;
		color: string;
		kind: 'line' | 'polyline';
		points: Array<[number, number]>;
		locked?: boolean;
	}>;
};
```

### Contract Rules

- The host bootstrap is authoritative for project, selected videos, line ids, frame count, and initial line geometry.
- The React overlay must resync its local state whenever Streamlit reruns with new component args.
- The React overlay must never write to the database or API directly; it only emits snapshots and action intent through the host bridge.
- The Streamlit page is responsible for turning the overlay snapshot into API calls for persistence, recounting, and export.
- Bootstrap and snapshot payloads must be serializable JSON values with no functions, Dates, or cyclic references.
- The bridge wrapper must run as a Streamlit custom component declared from Python.
- The frontend must only emit values through `Streamlit.setComponentValue(...)` and must not rely on raw `postMessage` as the canonical return path.
- HTML embed transport is allowed only as a temporary diagnostics fallback and is not the production contract path.
- If the component frontend cannot load, the host must surface a degraded state and never claim overlay connection.
- Host-side reconciliation must be idempotent and snapshot-deduplicated to avoid duplicate create/update/delete operations across reruns.
- Host-side count requests must execute after persistence reconciliation so line ids are authoritative.

## Invariants

- The overlay must never mutate database state directly.
- Visual edits are local until explicit confirmation or API persistence rules fire.
- Saved lines remain the canonical source of truth for export and counts.
- The React component and Streamlit host must agree on the selected workspace, selected videos, and line identifiers.

## API Contract Surface

- `GET /projects/{project_id}/videos`
- `GET /videos/{video_id}/frame-url`
- `GET /videos/{video_id}/trajectories-url`
- `GET /videos/{video_id}/heatmap-url`
- `GET /videos/{video_id}/track-stats`
- `GET /projects/{project_id}/lines`
- `POST /projects/{project_id}/lines`
- `PATCH /lines/{line_id}`
- `DELETE /lines/{line_id}`
- `POST /projects/{project_id}/counts`
- `POST /projects/{project_id}/export`

## UI Contract Surface

- Central viewport: 100-frame synchronized slider with frame thumbnails or pre-extracted JPGs.
- Persistent overlay: all track lines visible, toggleable by class, direction, and custom groups.
- Side controls: line list, counts, totals, auto-suggest, heatmap toggle, rose diagram, and loose-track sectors.
- Drawing tools: add/edit/delete straight or polyline lines with seamless direct manipulation.

## Planned Implementation Targets

- `frontend/pages/2_Count_and_export_hybrid.py`
- `frontend/hybrid_viewport/` React/Vite component bundle
- `frontend/api_client.py` bridge calls used by the host page and embedded component

## Integration Verification Checklist

- Streamlit passes bootstrap payload through declared component args on each rerun.
- React receives args via the Streamlit component render event or React wrapper props.
- React emits overlay snapshots through `Streamlit.setComponentValue(...)`.
- The payload shapes remain stable across line selection, frame scrubbing, line edits, and layer toggles.
- The Streamlit page no longer reports `Unrecognized component API version: 'undefined'`.
- The bridge handshake emits ready/render semantics once per mount and remains stable on reruns.
- The component uses a Vite dev URL in development and static built assets in production.
- Production `dist/index.html` references component assets through relative paths (`./assets/...`).
- Browser console contains no module-script MIME mismatch for component assets.
