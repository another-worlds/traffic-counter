import { useEffect, useMemo, useState } from 'react';
import type { OverlayModel, ViewportSpec, LineGeometry, LayerKey } from './viewportState';
import { buildBridgePayload, createDefaultOverlayModel, createDemoLine, reduceOverlayModel } from './viewportState';
import './styles.css';

type AppProps = {
  spec: ViewportSpec;
  initialLines?: LineGeometry[];
};

const DEFAULT_SPEC: ViewportSpec = {
  projectId: 'preview',
  videoIds: [],
  selectedLineIds: [],
  frameCount: 100,
  activeLayers: ['saved-lines', 'frame-scrubber', 'direction-overlay', 'counts'],
};

const VIEWPORT_WIDTH = 1920;
const VIEWPORT_HEIGHT = 1080;

export default function App({ spec = DEFAULT_SPEC, initialLines = [] }: AppProps) {
  const initialModel = useMemo(() => createDefaultOverlayModel(spec, initialLines), [spec, initialLines]);
  const [model, setModel] = useState<OverlayModel>(initialModel);
  const bridgePayload = useMemo(() => buildBridgePayload(model), [model]);
  const [bridgeStatus, setBridgeStatus] = useState<'pending' | 'sent'>('pending');

  useEffect(() => {
    setModel(initialModel);
    setBridgeStatus('pending');
  }, [initialModel]);

  function dispatch(action: Parameters<typeof reduceOverlayModel>[1]) {
    setModel((current) => reduceOverlayModel(current, action));
  }

  function addLine() {
    dispatch({ type: 'add-line', line: createDemoLine(model.lines.length) });
  }

  function renameSelectedLine() {
    if (!model.selectedLineId) {
      return;
    }
    const nextName = window.prompt('Rename selected line', model.lines.find((line) => line.id === model.selectedLineId)?.name ?? 'line');
    if (!nextName) {
      return;
    }
    dispatch({ type: 'update-line', lineId: model.selectedLineId, patch: { name: nextName.trim() } });
  }

  function deleteSelectedLine() {
    if (!model.selectedLineId) {
      return;
    }
    dispatch({ type: 'delete-line', lineId: model.selectedLineId });
  }

  const selectedLine = model.lines.find((line) => line.id === model.selectedLineId) ?? null;

  useEffect(() => {
    if (typeof window === 'undefined') {
      return;
    }

    const message = {
      source: 'traffic-counter-hybrid-viewport',
      payload: bridgePayload,
    };

    window.parent.postMessage(message, '*');
    window.dispatchEvent(new CustomEvent('traffic-counter:overlay-snapshot', { detail: message }));
    setBridgeStatus('sent');
  }, [bridgePayload]);

  return (
    <div className="overlay-shell">
      <header className="overlay-header">
        <div>
          <p className="eyebrow">Hybrid viewport</p>
          <h1>Counting line editor</h1>
        </div>
        <div className="frame-pill">
          Frame {model.currentFrame + 1} / {Math.max(model.spec.frameCount, 1)}
        </div>
      </header>

      <main className="overlay-grid">
        <section className="viewport-panel">
          <div className="viewport-stage">
            <svg
              className="viewport-svg"
              viewBox={`0 0 ${VIEWPORT_WIDTH} ${VIEWPORT_HEIGHT}`}
              role="img"
              aria-label="Interactive counting overlay viewport"
            >
              <defs>
                <pattern id="viewport-grid" width="96" height="96" patternUnits="userSpaceOnUse">
                  <path d="M 96 0 L 0 0 0 96" fill="none" stroke="rgba(255,255,255,0.05)" strokeWidth="2" />
                </pattern>
              </defs>

              <rect width={VIEWPORT_WIDTH} height={VIEWPORT_HEIGHT} fill="url(#viewport-grid)" />

              <text x="64" y="78" className="viewport-overlay-label">
                Hybrid viewport ready
              </text>

              <text x="64" y="116" className="viewport-overlay-subtitle">
                {selectedLine ? `Selected: ${selectedLine.name}` : 'Select a line to edit it' }
              </text>

              {model.lines.map((line) => {
                const points = line.points.map(([x, y]) => `${x},${y}`).join(' ');
                const isSelected = line.id === model.selectedLineId;
                const lineClass = isSelected ? 'viewport-line selected' : 'viewport-line';

                return (
                  <g key={line.id} className={lineClass} onClick={() => dispatch({ type: 'select-line', lineId: line.id })}>
                    {line.kind === 'polyline' ? (
                      <polyline points={points} stroke={line.color} strokeWidth={isSelected ? 10 : 7} fill="none" strokeLinecap="round" strokeLinejoin="round" />
                    ) : (
                      <line x1={line.points[0]?.[0] ?? 0} y1={line.points[0]?.[1] ?? 0} x2={line.points.at(-1)?.[0] ?? 0} y2={line.points.at(-1)?.[1] ?? 0} stroke={line.color} strokeWidth={isSelected ? 10 : 7} strokeLinecap="round" />
                    )}
                    {line.points.map(([x, y], index) => (
                      <circle key={`${line.id}-point-${index}`} cx={x} cy={y} r={isSelected ? 16 : 12} fill={line.color} stroke="white" strokeWidth="4" />
                    ))}
                    <text x={line.points[0]?.[0] ?? 0} y={(line.points[0]?.[1] ?? 0) - 22} className="viewport-line-label">
                      {line.name}
                    </text>
                  </g>
                );
              })}

              {model.visibleLayers.heatmap ? <rect width={VIEWPORT_WIDTH} height={VIEWPORT_HEIGHT} className="viewport-heatmap-overlay" /> : null}
              {model.visibleLayers.suggestions ? <circle cx={VIEWPORT_WIDTH * 0.72} cy={VIEWPORT_HEIGHT * 0.24} r={92} className="viewport-suggestion-ring" /> : null}
            </svg>
          </div>

          <input
            className="frame-slider"
            type="range"
            min={0}
            max={Math.max(model.spec.frameCount - 1, 0)}
            value={model.currentFrame}
            onChange={(event) => dispatch({ type: 'set-frame', frame: Number(event.target.value) })}
          />

          <div className="toolbar-row">
            <button type="button" className="toolbar-button primary" onClick={addLine}>
              Add demo line
            </button>
            <button type="button" className="toolbar-button" onClick={renameSelectedLine} disabled={!model.selectedLineId}>
              Rename selected
            </button>
            <button type="button" className="toolbar-button danger" onClick={deleteSelectedLine} disabled={!model.selectedLineId}>
              Delete selected
            </button>
          </div>

          <div className="layer-strip">
            {(Object.keys(model.visibleLayers) as LayerKey[]).map((layer) => (
              <button
                key={layer}
                type="button"
                className={model.visibleLayers[layer] ? 'layer-chip active' : 'layer-chip'}
                onClick={() => dispatch({ type: 'toggle-layer', layer })}
              >
                {layer}
              </button>
            ))}
          </div>
        </section>

        <aside className="side-panel">
          <div className="panel-card">
            <h2>Viewport spec</h2>
            <pre>{JSON.stringify(model.spec, null, 2)}</pre>
          </div>
          <div className="panel-card">
            <h2>Bridge payload</h2>
            <p className="muted">Bridge status: {bridgeStatus}</p>
            <pre>{JSON.stringify(bridgePayload, null, 2)}</pre>
          </div>
          <div className="panel-card">
            <h2>Editing state</h2>
            <p>Selected line: {model.selectedLineId ?? 'none'}</p>
            <p>Saved lines loaded: {model.lines.length}</p>
          </div>
          <div className="panel-card">
            <h2>Lines</h2>
            {model.lines.length === 0 ? (
              <p className="muted">No lines loaded yet.</p>
            ) : (
              <div className="line-list">
                {model.lines.map((line) => (
                  <button
                    key={line.id}
                    type="button"
                    className={line.id === model.selectedLineId ? 'line-row active' : 'line-row'}
                    onClick={() => dispatch({ type: 'select-line', lineId: line.id })}
                  >
                    <span className="line-swatch" style={{ backgroundColor: line.color }} />
                    <span className="line-label">{line.name}</span>
                    <span className="line-kind">{line.kind}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
          <div className="panel-card">
            <h2>Planned actions</h2>
            <ul>
              <li>Drag lines and endpoints</li>
              <li>Toggle heatmap and direction overlays</li>
              <li>Auto-suggest perpendicular lines from track clusters</li>
            </ul>
          </div>
        </aside>
      </main>
    </div>
  );
}
