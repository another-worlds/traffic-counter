import React from 'react';
import type {
  CountsBundle,
  HostViewportBootstrap,
  LayerKey,
  LineGeometry,
  OverlayAction,
  OverlayModel,
  PendingAction,
  Point,
  Suggestion,
  TrackStats,
  ViewportSpec,
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
import './styles.css';

type AppProps = {
  bootstrap?: HostViewportBootstrap;
  onSnapshot?: (payload: ReturnType<typeof buildBridgePayload>) => void;
};

const DEFAULT_VIDEO_SIZE: VideoSize = { width: 1920, height: 1080 };
const MOVE_THRESHOLD = 3; // source pixels — distinguish click-select from drag

export default function App({ bootstrap, onSnapshot }: AppProps) {
  const spec: ViewportSpec = React.useMemo(() => buildViewportSpecFromBootstrap(bootstrap), [bootstrap]);
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
  const [drawingColor, setDrawingColor] = React.useState('#e24b4a');
  const lastEmittedRef = React.useRef<string>('');
  const pendingSelectRef = React.useRef<{ lineId: string; anchor: Point } | null>(null);
  const lastProjectIdRef = React.useRef<string>(spec.projectId);

  // Reconcile with new bootstrap from host: replace lines only (preserve UI state).
  // Full reset only if project changed.
  React.useEffect(() => {
    setModel((prev) => {
      // Don't touch the model mid-interaction.
      if (prev.interaction.kind !== 'idle') return prev;
      // New project → fresh start.
      if (lastProjectIdRef.current !== spec.projectId) {
        lastProjectIdRef.current = spec.projectId;
        return initialModel;
      }
      // Same project → refresh lines but preserve selection/frame/layers.
      return reduceOverlayModel(prev, { type: 'replace-lines', lines: initialLines });
    });
  }, [spec.projectId, initialLines, initialModel]);

  const dispatch = React.useCallback((action: OverlayAction) => {
    setModel((current) => reduceOverlayModel(current, action));
  }, []);

  // Emit snapshot whenever the model changes — but only when interaction is idle (committed).
  React.useEffect(() => {
    if (model.interaction.kind !== 'idle') return;
    const payload = buildBridgePayload(model);
    const serialized = JSON.stringify(payload);
    if (serialized === lastEmittedRef.current) return;
    lastEmittedRef.current = serialized;
    onSnapshot?.(payload);
    if (model.pendingActions.length > 0) {
      // Clear after emitting to avoid resending.
      dispatch({ type: 'clear-pending-actions' });
    }
  }, [model, onSnapshot, dispatch]);

  // Keyboard handlers
  React.useEffect(() => {
    function handleKey(e: KeyboardEvent) {
      // Don't intercept when typing in an input field.
      const tag = (e.target as HTMLElement)?.tagName ?? '';
      if (tag === 'INPUT' || tag === 'TEXTAREA') return;
      if (model.selectedLineId && (e.key === 'Delete' || e.key === 'Backspace')) {
        e.preventDefault();
        dispatch({ type: 'delete-line', lineId: model.selectedLineId });
      } else if (e.key === 'Escape') {
        if (model.interaction.kind === 'drawing') {
          dispatch({ type: 'cancel-draft' });
        } else {
          dispatch({ type: 'select-line', lineId: null });
        }
      }
    }
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [model.selectedLineId, model.interaction.kind, dispatch]);

  // ── Mouse handlers ─────────────────────────────────────────────────────────

  const handleMouseDownEmpty = React.useCallback(
    (point: Point) => {
      if (model.interaction.kind !== 'idle') return;
      // Click on empty viewport starts drawing a new line.
      dispatch({ type: 'select-line', lineId: null });
      dispatch({ type: 'start-draw', point, color: drawingColor });
    },
    [model.interaction.kind, dispatch, drawingColor],
  );

  const handleMouseDownLine = React.useCallback(
    (lineId: string, point: Point) => {
      if (model.interaction.kind !== 'idle') return;
      // Defer starting move until first movement — so click-only = select-only.
      pendingSelectRef.current = { lineId, anchor: point };
      dispatch({ type: 'select-line', lineId });
    },
    [model.interaction.kind, dispatch],
  );

  const handleMouseDownHandle = React.useCallback(
    (lineId: string, handleIndex: number) => {
      if (model.interaction.kind !== 'idle') return;
      pendingSelectRef.current = null;
      dispatch({ type: 'start-resize-handle', lineId, handleIndex });
    },
    [model.interaction.kind, dispatch],
  );

  const handleMouseMove = React.useCallback(
    (point: Point) => {
      const pending = pendingSelectRef.current;
      if (pending) {
        const dx = point[0] - pending.anchor[0];
        const dy = point[1] - pending.anchor[1];
        if (Math.hypot(dx, dy) > MOVE_THRESHOLD) {
          // Promote to a move
          pendingSelectRef.current = null;
          dispatch({ type: 'start-move', lineId: pending.lineId, anchor: pending.anchor });
          dispatch({ type: 'update-move', point });
        }
        return;
      }
      if (model.interaction.kind === 'drawing') {
        dispatch({ type: 'update-draft', point });
      } else if (model.interaction.kind === 'moving') {
        dispatch({ type: 'update-move', point });
      } else if (model.interaction.kind === 'resizing') {
        dispatch({ type: 'update-resize-handle', point });
      }
    },
    [model.interaction.kind, dispatch],
  );

  const handleMouseUp = React.useCallback(
    (_point: Point) => {
      pendingSelectRef.current = null;
      if (model.interaction.kind === 'drawing') {
        dispatch({ type: 'commit-draft' });
      } else if (model.interaction.kind === 'moving') {
        dispatch({ type: 'commit-move' });
      } else if (model.interaction.kind === 'resizing') {
        dispatch({ type: 'commit-resize-handle' });
      }
    },
    [model.interaction.kind, dispatch],
  );

  // ── Side-panel actions ────────────────────────────────────────────────────

  const handleUpdateLine = React.useCallback(
    (lineId: string, patch: Partial<LineGeometry>) => {
      dispatch({ type: 'update-line', lineId, patch });
    },
    [dispatch],
  );

  const handleDeleteLine = React.useCallback(
    (lineId: string) => {
      dispatch({ type: 'delete-line', lineId });
    },
    [dispatch],
  );

  const handleSelectLine = React.useCallback(
    (lineId: string | null) => {
      dispatch({ type: 'select-line', lineId });
    },
    [dispatch],
  );

  const handleToggleLayer = React.useCallback(
    (layer: LayerKey) => {
      dispatch({ type: 'toggle-layer', layer });
    },
    [dispatch],
  );

  const queueAction = React.useCallback(
    (action: PendingAction) => {
      dispatch({ type: 'queue-action', action });
    },
    [dispatch],
  );

  return (
    <div className="overlay-shell">
      <header className="overlay-header">
        <div>
          <p className="eyebrow">Hybrid viewport</p>
          <h1>Counting line editor</h1>
        </div>
        <div className="header-info">
          <div className="frame-pill">
            Frame {model.currentFrame + 1} / {Math.max(model.spec.frameCount, 1)}
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
            videoSize={videoSize}
            frameUrl={frameUrl}
            trajectoriesUrl={trajectoriesUrl}
            heatmapUrl={heatmapUrl}
            onMouseDownEmpty={handleMouseDownEmpty}
            onMouseDownLine={handleMouseDownLine}
            onMouseDownHandle={handleMouseDownHandle}
            onMouseMove={handleMouseMove}
            onMouseUp={handleMouseUp}
          />

          <input
            className="frame-slider"
            type="range"
            min={0}
            max={Math.max(model.spec.frameCount - 1, 0)}
            value={model.currentFrame}
            onChange={(event) => dispatch({ type: 'set-frame', frame: Number(event.target.value) })}
          />
        </section>

        <SidePanel
          model={model}
          trackStats={trackStats}
          counts={counts}
          suggestions={suggestions}
          drawingColor={drawingColor}
          onDrawingColorChange={setDrawingColor}
          onUpdateLine={handleUpdateLine}
          onDeleteLine={handleDeleteLine}
          onSelectLine={handleSelectLine}
          onToggleLayer={handleToggleLayer}
          onRequestSuggestions={(n) => queueAction({ type: 'request-suggestions', n })}
          onAcceptSuggestion={(s) => queueAction({ type: 'accept-suggestion', suggestion: s })}
          onDismissSuggestions={() => queueAction({ type: 'dismiss-suggestions' })}
        />
      </main>
    </div>
  );
}
