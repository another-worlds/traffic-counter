import React from 'react';
import type {
  CountsBundle,
  HostViewportBootstrap,
  LineGeometry,
  OverlayAction,
  OverlayModel,
  Point,
  Suggestion,
  TrackStats,
  VideoSize,
} from './viewportState';
import {
  buildInitialLinesFromBootstrap,
  buildViewportSpecFromBootstrap,
  createDefaultOverlayModel,
  reduceOverlayModel,
} from './viewportState';
import {
  type ApiBaseConfig,
  type ApiLineCreatePayload,
  type ApiLineUpdatePayload,
  computeCounts,
  countsApiToBundle,
  createLine as apiCreateLine,
  deleteLine as apiDeleteLine,
  requestSuggestions as apiRequestSuggestions,
  updateLine as apiUpdateLine,
} from './api';
import Viewport from './Viewport';
import SidePanel from './SidePanel';
import { getTool } from './tools';
import type { ToolContext } from './tools';
import './styles.css';

type AppProps = {
  bootstrap?: HostViewportBootstrap;
};

const DEFAULT_VIDEO_SIZE: VideoSize = { width: 1920, height: 1080 };
const PATCH_DEBOUNCE_MS = 250;
const COUNTS_DEBOUNCE_MS = 300;

function lineToCreatePayload(line: LineGeometry): ApiLineCreatePayload {
  const a = line.points[0] ?? [0, 0];
  const b = line.points[line.points.length - 1] ?? [0, 0];
  return {
    name: line.name || 'line',
    color: line.color || '#e24b4a',
    points: { a: [a[0], a[1]], b: [b[0], b[1]] },
  };
}

function lineGeomEquals(a: LineGeometry, b: LineGeometry): boolean {
  if (a.name !== b.name || a.color !== b.color) return false;
  if (a.points.length !== b.points.length) return false;
  for (let i = 0; i < a.points.length; i++) {
    if (Math.abs(a.points[i][0] - b.points[i][0]) > 0.5) return false;
    if (Math.abs(a.points[i][1] - b.points[i][1]) > 0.5) return false;
  }
  return true;
}

function linePatch(prev: LineGeometry, next: LineGeometry): ApiLineUpdatePayload {
  const patch: ApiLineUpdatePayload = {};
  if (prev.name !== next.name) patch.name = next.name;
  if (prev.color !== next.color) patch.color = next.color;
  const ap = prev.points[0] ?? [0, 0];
  const bp = prev.points[prev.points.length - 1] ?? [0, 0];
  const an = next.points[0] ?? [0, 0];
  const bn = next.points[next.points.length - 1] ?? [0, 0];
  const pointsChanged =
    Math.abs(ap[0] - an[0]) > 0.5 || Math.abs(ap[1] - an[1]) > 0.5 ||
    Math.abs(bp[0] - bn[0]) > 0.5 || Math.abs(bp[1] - bn[1]) > 0.5;
  if (pointsChanged) {
    patch.points = { a: [an[0], an[1]], b: [bn[0], bn[1]] };
  }
  return patch;
}

export default function App({ bootstrap }: AppProps) {
  const spec = React.useMemo(() => buildViewportSpecFromBootstrap(bootstrap), [bootstrap]);
  const initialLines = React.useMemo(() => buildInitialLinesFromBootstrap(bootstrap), [bootstrap]);
  const videoSize: VideoSize = bootstrap?.videoSize ?? DEFAULT_VIDEO_SIZE;
  const trackStats: TrackStats | undefined = bootstrap?.trackStats;

  const apiCfg = React.useMemo<ApiBaseConfig>(
    () => ({ baseUrl: bootstrap?.apiBaseUrl ?? '' }),
    [bootstrap?.apiBaseUrl],
  );
  const apiCfgRef = React.useRef(apiCfg);
  apiCfgRef.current = apiCfg;

  const initialModel = React.useMemo(
    () => createDefaultOverlayModel(spec, initialLines),
    [spec, initialLines],
  );

  const [model, setModel] = React.useState<OverlayModel>(initialModel);
  const [counts, setCounts] = React.useState<CountsBundle | undefined>(bootstrap?.counts);
  const [suggestions, setSuggestions] = React.useState<Suggestion[] | undefined>(
    bootstrap?.suggestions,
  );
  const lastProjectIdRef = React.useRef<string>(spec.projectId);

  // Server-truth refs. Keyed by *server* id once a line has been persisted.
  // For lines that have not yet finished POSTing, their temp id lives only in
  // `pendingCreatesRef` — they are intentionally absent from serverLinesRef so
  // the diff effect skips PATCH/DELETE attempts against a not-yet-created row.
  const serverLinesRef = React.useRef<Map<string, LineGeometry>>(new Map());
  const pendingCreatesRef = React.useRef<Set<string>>(new Set());
  const patchTimersRef = React.useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map());
  const inflightPatchRef = React.useRef<Map<string, AbortController>>(new Map());
  const countsTimerRef = React.useRef<ReturnType<typeof setTimeout> | null>(null);
  const inflightCountsRef = React.useRef<AbortController | null>(null);

  // Seed server-truth from initial bootstrap.
  React.useEffect(() => {
    serverLinesRef.current = new Map(initialLines.map((l) => [l.id, l]));
  // Run only when projectId changes — see project-switch effect below.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Project switch → reset everything from a fresh bootstrap.
  React.useEffect(() => {
    if (lastProjectIdRef.current === spec.projectId) return;
    lastProjectIdRef.current = spec.projectId;
    setModel(initialModel);
    setCounts(bootstrap?.counts);
    setSuggestions(bootstrap?.suggestions);
    serverLinesRef.current = new Map(initialLines.map((l) => [l.id, l]));
    pendingCreatesRef.current.clear();
    for (const t of patchTimersRef.current.values()) clearTimeout(t);
    patchTimersRef.current.clear();
    for (const c of inflightPatchRef.current.values()) c.abort();
    inflightPatchRef.current.clear();
  }, [spec.projectId, initialModel, initialLines, bootstrap?.counts, bootstrap?.suggestions]);

  // Counts/suggestions: also resync if the bootstrap brings fresh ones for the
  // same project (e.g. Streamlit rerendered after a workspace-side button).
  React.useEffect(() => {
    if (bootstrap?.counts) setCounts(bootstrap.counts);
  }, [bootstrap?.counts]);
  React.useEffect(() => {
    if (bootstrap?.suggestions !== undefined) setSuggestions(bootstrap.suggestions);
  }, [bootstrap?.suggestions]);

  const dispatch = React.useCallback((action: OverlayAction) => {
    setModel((current) => reduceOverlayModel(current, action));
  }, []);

  const scheduleCountsRefresh = React.useCallback(() => {
    if (countsTimerRef.current) clearTimeout(countsTimerRef.current);
    countsTimerRef.current = setTimeout(() => {
      countsTimerRef.current = null;
      const lineIds = Array.from(serverLinesRef.current.keys());
      if (inflightCountsRef.current) inflightCountsRef.current.abort();
      if (lineIds.length === 0) {
        setCounts({ total_unique_tracks: 0, per_line: {} });
        return;
      }
      const ctrl = new AbortController();
      inflightCountsRef.current = ctrl;
      computeCounts(
        apiCfgRef.current,
        spec.projectId,
        { video_ids: spec.videoIds, line_ids: lineIds },
        ctrl.signal,
      )
        .then((resp) => setCounts(countsApiToBundle(resp)))
        .catch((err) => {
          if ((err as { name?: string })?.name !== 'AbortError') {
            // eslint-disable-next-line no-console
            console.error('counts refresh failed', err);
          }
        });
    }, COUNTS_DEBOUNCE_MS);
  }, [spec.projectId, spec.videoIds]);

  // Core sync: diff React lines against server-truth and issue create/patch/delete.
  React.useEffect(() => {
    if (model.interaction.kind !== 'idle') return;
    if (!apiCfgRef.current.baseUrl) return;

    const localById = new Map(model.lines.map((l) => [l.id, l]));

    // CREATE — local ids that are neither persisted nor in-flight.
    for (const line of model.lines) {
      if (serverLinesRef.current.has(line.id)) continue;
      if (pendingCreatesRef.current.has(line.id)) continue;
      const [a, b] = [line.points[0], line.points[line.points.length - 1]];
      if (!a || !b) continue;
      pendingCreatesRef.current.add(line.id);
      const tempId = line.id;
      apiCreateLine(apiCfgRef.current, spec.projectId, lineToCreatePayload(line))
        .then((created) => {
          const serverId = String(created.id);
          const adapted: LineGeometry = {
            id: serverId,
            name: created.name ?? line.name,
            color: created.color ?? line.color,
            kind: 'line',
            points: [
              [created.points.a[0], created.points.a[1]],
              [created.points.b[0], created.points.b[1]],
            ],
          };
          serverLinesRef.current.set(serverId, adapted);
          // Swap the temp id in the model with the server id so subsequent
          // PATCH/DELETE in the diff effect address the persisted row.
          setModel((current) => {
            const exists = current.lines.some((l) => l.id === tempId);
            if (!exists) return current;
            const nextLines = current.lines.map((l) =>
              l.id === tempId ? { ...l, id: serverId } : l,
            );
            return {
              ...current,
              lines: nextLines,
              selectedLineId:
                current.selectedLineId === tempId ? serverId : current.selectedLineId,
            };
          });
          scheduleCountsRefresh();
        })
        .catch((err) => {
          // eslint-disable-next-line no-console
          console.error('create line failed', err);
          // Roll back the optimistic add.
          setModel((current) => ({
            ...current,
            lines: current.lines.filter((l) => l.id !== tempId),
            selectedLineId:
              current.selectedLineId === tempId ? null : current.selectedLineId,
          }));
        })
        .finally(() => {
          pendingCreatesRef.current.delete(tempId);
        });
    }

    // UPDATE — server lines whose geometry differs locally.
    for (const [serverId, prev] of serverLinesRef.current.entries()) {
      const local = localById.get(serverId);
      if (!local) continue;
      if (lineGeomEquals(prev, local)) continue;
      const patch = linePatch(prev, local);
      if (Object.keys(patch).length === 0) continue;
      // Snapshot the desired state so the timer captures the latest values.
      const desired = local;
      const existingTimer = patchTimersRef.current.get(serverId);
      if (existingTimer) clearTimeout(existingTimer);
      const timer = setTimeout(() => {
        patchTimersRef.current.delete(serverId);
        const prevController = inflightPatchRef.current.get(serverId);
        if (prevController) prevController.abort();
        const ctrl = new AbortController();
        inflightPatchRef.current.set(serverId, ctrl);
        apiUpdateLine(apiCfgRef.current, serverId, patch, ctrl.signal)
          .then((updated) => {
            inflightPatchRef.current.delete(serverId);
            serverLinesRef.current.set(serverId, {
              id: serverId,
              name: updated.name ?? desired.name,
              color: updated.color ?? desired.color,
              kind: 'line',
              points: [
                [updated.points.a[0], updated.points.a[1]],
                [updated.points.b[0], updated.points.b[1]],
              ],
            });
            scheduleCountsRefresh();
          })
          .catch((err) => {
            if ((err as { name?: string })?.name === 'AbortError') return;
            // eslint-disable-next-line no-console
            console.error('update line failed', err);
          });
      }, PATCH_DEBOUNCE_MS);
      patchTimersRef.current.set(serverId, timer);
    }

    // DELETE — server lines absent locally.
    for (const serverId of Array.from(serverLinesRef.current.keys())) {
      if (localById.has(serverId)) continue;
      serverLinesRef.current.delete(serverId);
      const pendingTimer = patchTimersRef.current.get(serverId);
      if (pendingTimer) { clearTimeout(pendingTimer); patchTimersRef.current.delete(serverId); }
      const pendingPatch = inflightPatchRef.current.get(serverId);
      if (pendingPatch) { pendingPatch.abort(); inflightPatchRef.current.delete(serverId); }
      apiDeleteLine(apiCfgRef.current, serverId)
        .then(() => scheduleCountsRefresh())
        .catch((err) => {
          // eslint-disable-next-line no-console
          console.error('delete line failed', err);
        });
    }
  }, [model.lines, model.interaction.kind, spec.projectId, scheduleCountsRefresh]);

  // Route keyboard events through the active tool.
  React.useEffect(() => {
    function handleKey(e: KeyboardEvent) {
      const tool = getTool(model.activeTool);
      if (!tool?.onKeyDown) return;
      const ctx: ToolContext = { model, dispatch, videoSize };
      tool.onKeyDown(e, ctx);
    }
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [model, dispatch, videoSize]);

  // Build tool context once per render so callbacks below stay stable.
  const toolCtxRef = React.useRef<ToolContext>({ model, dispatch, videoSize });
  toolCtxRef.current = { model, dispatch, videoSize };

  const handleMouseDownEmpty = React.useCallback((point: Point) => {
    const tool = getTool(toolCtxRef.current.model.activeTool);
    tool?.onMouseDownEmpty?.(point, toolCtxRef.current);
  }, []);

  const handleMouseDownLine = React.useCallback((lineId: string, point: Point) => {
    const tool = getTool(toolCtxRef.current.model.activeTool);
    tool?.onMouseDownLine?.(lineId, point, toolCtxRef.current);
  }, []);

  const handleMouseDownHandle = React.useCallback((lineId: string, handleIndex: number) => {
    const tool = getTool(toolCtxRef.current.model.activeTool);
    tool?.onMouseDownHandle?.(lineId, handleIndex, toolCtxRef.current);
  }, []);

  const handleMouseMove = React.useCallback((point: Point) => {
    const tool = getTool(toolCtxRef.current.model.activeTool);
    tool?.onMouseMove?.(point, toolCtxRef.current);
  }, []);

  const handleMouseUp = React.useCallback((point: Point) => {
    const tool = getTool(toolCtxRef.current.model.activeTool);
    tool?.onMouseUp?.(point, toolCtxRef.current);
  }, []);

  const handleRequestSuggestions = React.useCallback(
    (n: number) => {
      if (!apiCfgRef.current.baseUrl) return;
      apiRequestSuggestions(apiCfgRef.current, spec.projectId, {
        video_ids: spec.videoIds,
        n: Math.max(1, Math.min(10, n)),
      })
        .then((resp) => setSuggestions(resp))
        .catch((err) => {
          // eslint-disable-next-line no-console
          console.error('suggest-lines failed', err);
        });
    },
    [spec.projectId, spec.videoIds],
  );

  const handleAcceptSuggestion = React.useCallback(
    (s: Suggestion) => {
      const id =
        typeof crypto !== 'undefined' && crypto.randomUUID
          ? crypto.randomUUID()
          : `line-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      dispatch({
        type: 'add-line',
        line: {
          id,
          name: s.name,
          color: s.color,
          kind: 'line',
          points: [s.points.a, s.points.b],
        },
      });
      setSuggestions(undefined);
    },
    [dispatch],
  );

  const handleDismissSuggestions = React.useCallback(() => {
    setSuggestions(undefined);
  }, []);

  return (
    <div className="overlay-shell">
      <header className="overlay-header">
        <div>
          <p className="eyebrow">Counting line editor</p>
          <h1>Traffic Counter</h1>
        </div>
        <div className="header-info">
          <div className="frame-pill">
            {(() => {
              const total = Math.max(model.spec.frameCount, 1);
              if (total === 1) return 'Single camera angle';
              const entry = bootstrap?.frames?.[model.currentFrame];
              if (entry) return `Scene ${model.currentFrame + 1} / ${total} · ${entry.time_s.toFixed(1)}s`;
              return `Frame ${model.currentFrame + 1} / ${total}`;
            })()}
          </div>
          <div className="instruction-pill">
            Click-drag empty area → new line · drag line → move · drag handle → resize · Delete → remove
          </div>
        </div>
      </header>

      <main className="overlay-grid">
        <section className="viewport-panel">
          <Viewport
            model={model}
            bootstrap={bootstrap ?? {}}
            videoSize={videoSize}
            counts={counts}
            onMouseDownEmpty={handleMouseDownEmpty}
            onMouseDownLine={handleMouseDownLine}
            onMouseDownHandle={handleMouseDownHandle}
            onMouseMove={handleMouseMove}
            onMouseUp={handleMouseUp}
          />

          {model.spec.frameCount > 1 ? (
            <input
              className="frame-slider"
              type="range"
              min={0}
              max={model.spec.frameCount - 1}
              value={model.currentFrame}
              onChange={(e) => dispatch({ type: 'set-frame', frame: Number(e.target.value) })}
            />
          ) : (
            <p className="muted" style={{ margin: '10px 0 0' }}>
              Single camera angle — no scene cuts detected.
            </p>
          )}
        </section>

        <SidePanel
          model={model}
          bootstrap={bootstrap ?? {}}
          trackStats={trackStats}
          counts={counts}
          suggestions={suggestions}
          drawingColor={model.drawingColor}
          activeTool={model.activeTool}
          onDrawingColorChange={(color) => dispatch({ type: 'set-drawing-color', color })}
          onUpdateLine={(lineId, patch) => dispatch({ type: 'update-line', lineId, patch })}
          onDeleteLine={(lineId) => dispatch({ type: 'delete-line', lineId })}
          onSelectLine={(lineId) => dispatch({ type: 'select-line', lineId })}
          onToggleLayer={(layer) => dispatch({ type: 'toggle-layer', layer })}
          onRequestSuggestions={handleRequestSuggestions}
          onAcceptSuggestion={handleAcceptSuggestion}
          onDismissSuggestions={handleDismissSuggestions}
          dispatch={dispatch}
        />
      </main>
    </div>
  );
}
