# Counting Line Overlay Semantic Contract

Status: [IN_PROGRESS]

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

The Streamlit host page is the orchestration shell. The React/Vite overlay is the interactive viewport. Their communication is explicit and bidirectional, and it goes through a Streamlit HTML embed bridge that inlines a browser-reachable React guest document:

- Host -> React: bootstrap payload injected before mount or delivered after mount via `traffic-counter-host-shell` message.
- React -> Host: overlay snapshot emitted on every meaningful viewport change via `traffic-counter-hybrid-viewport` postMessage and a local custom event.

The bridge must not use the Streamlit custom-component API handshake. A runtime error such as `Unrecognized component API version: 'undefined'` is a contract violation and means the integration is still using the wrong transport.
The React guest document must be reachable in the browser. A localhost-based external guest URL is development-only and is not a valid production contract.

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
- The React overlay must resync its local state whenever a new bootstrap payload arrives.
- The React overlay must never write to the database or API directly; it only emits snapshots and action intent through the host bridge.
- The Streamlit page is responsible for turning the overlay snapshot into API calls for persistence, recounting, and export.
- Bootstrap and snapshot payloads must be serializable JSON values with no functions, Dates, or cyclic references.
- The bridge wrapper must run as a Streamlit HTML embed and relay messages to the React guest iframe.
- The bridge wrapper must never emit custom-component `apiVersion` messages in this mode.
- A raw iframe-only page is not sufficient for production integration; the HTML embed must at minimum relay bootstrap payloads and overlay snapshots.
- If the React guest document is not reachable, the bridge must surface a degraded state and not claim the overlay is connected.

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

- Streamlit emits the bootstrap payload before or immediately after the React root mounts.
- React consumes bootstrap payloads from `window.__TRAFFIC_COUNTER_HYBRID_VIEWPORT__` and from `traffic-counter-host-shell` messages.
- React emits overlay snapshots through `window.parent.postMessage` using the `traffic-counter-hybrid-viewport` source tag.
- The payload shapes remain stable across line selection, frame scrubbing, line edits, and layer toggles.
- The Streamlit page no longer reports `Unrecognized component API version: 'undefined'`.
- The bridge messages are exchanged only through the HTML embed and `postMessage`.
- The React guest is served from a browser-reachable document, not an assumed localhost-only endpoint.
