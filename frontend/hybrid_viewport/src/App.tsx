import React from 'react';
import type {
  CountsBundle,
  HostViewportBootstrap,
  OverlayAction,
  OverlayModel,
  Point,
  Suggestion,
  TrackStats,
  VideoSize,
} from './viewportState';
import {
  buildBridgePayload,
  buildInitialLinesFromBootstrap,
  buildViewportSpecFromBootstrap,
  createDefaultOverlayModel,
  reduceOverlayModel,
} from './viewportState';
import Viewport from './Viewport';
import SidePanel from './SidePanel';
import { getTool } from './tools';
import type { ToolContext } from './tools';
import './styles.css';

type AppProps = {
  bootstrap?: HostViewportBootstrap;
  onSnapshot?: (payload: ReturnType<typeof buildBridgePayload>) => void;
};

const DEFAULT_VIDEO_SIZE: VideoSize = { width: 1920, height: 1080 };

export default function App({ bootstrap, onSnapshot }: AppProps) {
  const spec = React.useMemo(() => buildViewportSpecFromBootstrap(bootstrap), [bootstrap]);
  const initialLines = React.useMemo(() => buildInitialLinesFromBootstrap(bootstrap), [bootstrap]);
  const videoSize: VideoSize = bootstrap?.videoSize ?? DEFAULT_VIDEO_SIZE;
  const frameUrl = bootstrap?.frameUrl;
  const trajectoriesUrl = bootstrap?.trajectoriesUrl;
  const heatmapUrl = bootstrap?.heatmapUrl;
  const trackStats: TrackStats | undefined = bootstrap?.trackStats;
  const counts: CountsBundle | undefined = bootstrap?.counts;
  const suggestions: Suggestion[] | undefined = bootstrap?.suggestions;

  const initialModel = React.useMemo(
    () => createDefaultOverlayModel(spec, initialLines),
    [spec, initialLines],
  );

  const [model, setModel] = React.useState<OverlayModel>(initialModel);
  const lastEmittedRef = React.useRef<string>('');
  const lastProjectIdRef = React.useRef<string>(spec.projectId);

  // Reconcile bootstrap updates from host: lines only, preserve UI state.
  React.useEffect(() => {
    setModel((prev) => {
      if (prev.interaction.kind !== 'idle') return prev;
      if (lastProjectIdRef.current !== spec.projectId) {
        lastProjectIdRef.current = spec.projectId;
        return initialModel;
      }
      return reduceOverlayModel(prev, { type: 'replace-lines', lines: initialLines });
    });
  }, [spec.projectId, initialLines, initialModel]);

  const dispatch = React.useCallback((action: OverlayAction) => {
    setModel((current) => reduceOverlayModel(current, action));
  }, []);

  // Emit snapshot on idle transitions only.
  React.useEffect(() => {
    if (model.interaction.kind !== 'idle') return;
    const payload = buildBridgePayload(model);
    const serialized = JSON.stringify(payload);
    if (serialized === lastEmittedRef.current) return;
    lastEmittedRef.current = serialized;
    onSnapshot?.(payload);
    if (model.pendingActions.length > 0) {
      dispatch({ type: 'clear-pending-actions' });
      // Pre-mark the cleared version as emitted so the effect doesn't fire a second snapshot.
      const clearedPayload = buildBridgePayload({ ...model, pendingActions: [] });
      lastEmittedRef.current = JSON.stringify(clearedPayload);
    }
  }, [model, onSnapshot, dispatch]);

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
          onRequestSuggestions={(n) =>
            dispatch({ type: 'queue-action', action: { type: 'request-suggestions', n } })}
          onAcceptSuggestion={(s) => {
            const id = typeof crypto !== 'undefined' && crypto.randomUUID
              ? crypto.randomUUID()
              : `line-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
            dispatch({ type: 'add-line', line: {
              id, name: s.name, color: s.color, kind: 'line',
              points: [s.points.a, s.points.b],
            }});
            dispatch({ type: 'queue-action', action: { type: 'accept-suggestion', suggestion: s } });
          }}
          onDismissSuggestions={() =>
            dispatch({ type: 'queue-action', action: { type: 'dismiss-suggestions' } })}
          dispatch={dispatch}
        />
      </main>
    </div>
  );
}
