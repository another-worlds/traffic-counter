import React from 'react';
import type {
  CountsBundle,
  LayerKey,
  LineGeometry,
  OverlayModel,
  Suggestion,
  TrackStats,
} from './viewportState';
import RoseDiagram from './RoseDiagram';

type SidePanelProps = {
  model: OverlayModel;
  trackStats?: TrackStats;
  counts?: CountsBundle;
  suggestions?: Suggestion[];
  drawingColor: string;
  onDrawingColorChange: (color: string) => void;
  onUpdateLine: (lineId: string, patch: Partial<LineGeometry>) => void;
  onDeleteLine: (lineId: string) => void;
  onSelectLine: (lineId: string | null) => void;
  onToggleLayer: (layer: LayerKey) => void;
  onRequestSuggestions: (n: number) => void;
  onAcceptSuggestion: (s: Suggestion) => void;
  onDismissSuggestions: () => void;
};

const LAYER_ORDER: { key: LayerKey; label: string }[] = [
  { key: 'trajectories', label: 'Trajectories' },
  { key: 'heatmap', label: 'Heatmap' },
  { key: 'saved-lines', label: 'Lines' },
];

export default function SidePanel({
  model,
  trackStats,
  counts,
  suggestions,
  drawingColor,
  onDrawingColorChange,
  onUpdateLine,
  onDeleteLine,
  onSelectLine,
  onToggleLayer,
  onRequestSuggestions,
  onAcceptSuggestion,
  onDismissSuggestions,
}: SidePanelProps) {
  const [suggestN, setSuggestN] = React.useState(3);

  const perLine = counts?.per_line ?? {};
  const totalUnique = counts?.total_unique_tracks ?? trackStats?.total_tracks ?? 0;
  const sumAcross = Object.values(perLine).reduce((acc, l) => acc + (l?.total ?? 0), 0);

  return (
    <aside className="side-panel">
      {/* Summary card */}
      <div className="panel-card summary-card">
        <h2>Total crossings</h2>
        <div className="big-number">{sumAcross}</div>
        <p className="muted">
          {Object.keys(perLine).length} line(s) · {totalUnique} unique tracks
        </p>
      </div>

      {/* Layers */}
      <div className="panel-card">
        <h2>Layers</h2>
        <div className="layer-strip">
          {LAYER_ORDER.map((layer) => (
            <button
              key={layer.key}
              type="button"
              className={model.visibleLayers[layer.key] ? 'layer-chip active' : 'layer-chip'}
              onClick={() => onToggleLayer(layer.key)}
            >
              {layer.label}
            </button>
          ))}
        </div>
      </div>

      {/* Drawing color */}
      <div className="panel-card">
        <h2>Drawing color</h2>
        <div className="color-row">
          <input
            type="color"
            value={drawingColor}
            onChange={(e) => onDrawingColorChange(e.target.value)}
            className="color-input"
          />
          <span className="muted">Used for next line drawn</span>
        </div>
      </div>

      {/* Counting lines list */}
      <div className="panel-card">
        <h2>Counting lines</h2>
        {model.lines.length === 0 ? (
          <p className="muted">No lines yet. Click and drag on the viewport to draw one.</p>
        ) : (
          <div className="line-list">
            {model.lines.map((line) => {
              const lc = perLine[line.id];
              const total = lc?.total ?? 0;
              const pct = lc?.percent_of_video_total ?? 0;
              const dirPos = lc?.by_direction?.positive ?? 0;
              const dirNeg = lc?.by_direction?.negative ?? 0;
              const byClass = lc?.by_class ?? {};
              const topClass =
                Object.entries(byClass).sort((a, b) => b[1] - a[1])[0]?.[0] ?? '—';
              const isSelected = line.id === model.selectedLineId;
              return (
                <div
                  key={line.id}
                  className={isSelected ? 'line-card selected' : 'line-card'}
                  onClick={() => onSelectLine(line.id)}
                >
                  <div className="line-card-header">
                    <input
                      type="color"
                      value={line.color}
                      onClick={(e) => e.stopPropagation()}
                      onChange={(e) => onUpdateLine(line.id, { color: e.target.value })}
                      className="line-swatch-color"
                    />
                    <input
                      type="text"
                      value={line.name}
                      onClick={(e) => e.stopPropagation()}
                      onChange={(e) => onUpdateLine(line.id, { name: e.target.value })}
                      className="line-name-input"
                    />
                    <button
                      type="button"
                      className="line-delete-btn"
                      onClick={(e) => {
                        e.stopPropagation();
                        onDeleteLine(line.id);
                      }}
                      aria-label={`Delete ${line.name}`}
                    >
                      ✕
                    </button>
                  </div>
                  <div className="line-card-stats">
                    <span className="stat-pill stat-total">{total}</span>
                    <span className="stat-pill">{pct.toFixed(1)}%</span>
                    <span className="stat-pill stat-dir">▲ {dirPos}</span>
                    <span className="stat-pill stat-dir">▼ {dirNeg}</span>
                    <span className="stat-pill stat-class">{topClass}</span>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Auto-suggest */}
      <div className="panel-card">
        <h2>Auto-suggest lines</h2>
        <div className="suggest-row">
          <input
            type="number"
            min={1}
            max={10}
            value={suggestN}
            onChange={(e) => setSuggestN(Math.max(1, Math.min(10, Number(e.target.value) || 1)))}
            className="suggest-n-input"
          />
          <button
            type="button"
            className="toolbar-button primary"
            onClick={() => onRequestSuggestions(suggestN)}
          >
            ✨ Suggest
          </button>
          {suggestions && suggestions.length > 0 ? (
            <button type="button" className="toolbar-button" onClick={onDismissSuggestions}>
              Dismiss
            </button>
          ) : null}
        </div>
        {suggestions && suggestions.length > 0 ? (
          <div className="suggestion-list">
            {suggestions.map((s, i) => (
              <div key={`${s.name}-${i}`} className="suggestion-card">
                <div className="suggestion-meta">
                  <span className="line-swatch" style={{ backgroundColor: s.color }} />
                  <span className="suggestion-name">{s.name}</span>
                  <span className="muted">score {s.score}</span>
                </div>
                <button
                  type="button"
                  className="toolbar-button primary small"
                  onClick={() => onAcceptSuggestion(s)}
                >
                  Add
                </button>
              </div>
            ))}
          </div>
        ) : null}
      </div>

      {/* Direction rose diagram */}
      {trackStats ? (
        <div className="panel-card">
          <h2>Direction rose</h2>
          <RoseDiagram bins={trackStats.direction_bins} />
        </div>
      ) : null}

      {/* Class breakdown */}
      {trackStats?.by_class && Object.keys(trackStats.by_class).length > 0 ? (
        <div className="panel-card">
          <h2>Vehicle classes</h2>
          <div className="class-bars">
            {Object.entries(trackStats.by_class)
              .sort((a, b) => b[1] - a[1])
              .map(([cls, n]) => {
                const total = Object.values(trackStats.by_class!).reduce((a, b) => a + b, 0) || 1;
                const pct = Math.round((100 * n) / total);
                return (
                  <div key={cls} className="class-bar-row">
                    <span className="class-bar-label">{cls}</span>
                    <div className="class-bar-track">
                      <div className="class-bar-fill" style={{ width: `${pct}%` }} />
                    </div>
                    <span className="class-bar-val">
                      {n} ({pct}%)
                    </span>
                  </div>
                );
              })}
          </div>
        </div>
      ) : null}
    </aside>
  );
}
