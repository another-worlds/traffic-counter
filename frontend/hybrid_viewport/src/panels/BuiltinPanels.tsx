import React from 'react';
import RoseDiagram from '../RoseDiagram';
import type { PanelSection, PanelSectionProps } from './types';

// ── Summary ─────────────────────────────────────────────────────────────────

function SummaryPanel({ counts, trackStats }: PanelSectionProps) {
  const perLine = counts?.per_line ?? {};
  const totalUnique = counts?.total_unique_tracks ?? trackStats?.total_tracks ?? 0;
  const sumAcross = Object.values(perLine).reduce((acc, l) => acc + (l?.total ?? 0), 0);
  return (
    <div className="panel-card summary-card">
      <h2>Total crossings</h2>
      <div className="big-number">{sumAcross}</div>
      <p className="muted">
        {Object.keys(perLine).length} line(s) · {totalUnique} unique tracks
      </p>
    </div>
  );
}

// ── Layers ───────────────────────────────────────────────────────────────────

const LAYER_ORDER = [
  { key: 'trajectories' as const, label: 'Trajectories' },
  { key: 'heatmap' as const, label: 'Heatmap' },
  { key: 'saved-lines' as const, label: 'Lines' },
];

function LayersPanel({ model, onToggleLayer }: PanelSectionProps) {
  return (
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
  );
}

// ── Drawing color ────────────────────────────────────────────────────────────

function DrawingColorPanel({ drawingColor, onDrawingColorChange }: PanelSectionProps) {
  return (
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
  );
}

// ── Counting lines list ───────────────────────────────────────────────────────

function LineListPanel({ model, counts, onSelectLine, onUpdateLine, onDeleteLine }: PanelSectionProps) {
  const perLine = counts?.per_line ?? {};
  const isDraft = (id: string) =>
    model.interaction.kind === 'drawing' && model.interaction.draftLineId === id;

  return (
    <div className="panel-card">
      <h2>Counting lines</h2>
      {model.lines.filter((l) => !isDraft(l.id)).length === 0 ? (
        <p className="muted">No lines yet. Click and drag on the viewport to draw one.</p>
      ) : (
        <div className="line-list">
          {model.lines
            .filter((l) => !isDraft(l.id))
            .map((line) => {
              const lc = perLine[line.id];
              const total = lc?.total ?? 0;
              const pct = lc?.percent_of_video_total ?? 0;
              const dirPos = lc?.by_direction?.positive ?? 0;
              const dirNeg = lc?.by_direction?.negative ?? 0;
              const byClass = lc?.by_class ?? {};
              const topClass = Object.entries(byClass).sort((a, b) => b[1] - a[1])[0]?.[0] ?? '—';
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
                      onClick={(e) => { e.stopPropagation(); onDeleteLine(line.id); }}
                      aria-label={`Delete ${line.name}`}
                    >✕</button>
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
  );
}

// ── Auto-suggest ─────────────────────────────────────────────────────────────

function SuggestPanel({
  suggestions,
  onRequestSuggestions,
  onAcceptSuggestion,
  onDismissSuggestions,
}: PanelSectionProps) {
  const [suggestN, setSuggestN] = React.useState(3);
  return (
    <div className="panel-card">
      <h2>Auto-suggest lines</h2>
      <div className="suggest-row">
        <input
          type="number"
          min={1} max={10}
          value={suggestN}
          onChange={(e) => setSuggestN(Math.max(1, Math.min(10, Number(e.target.value) || 1)))}
          className="suggest-n-input"
        />
        <button type="button" className="toolbar-button primary" onClick={() => onRequestSuggestions(suggestN)}>
          ✨ Suggest
        </button>
        {suggestions && suggestions.length > 0 && (
          <button type="button" className="toolbar-button" onClick={onDismissSuggestions}>
            Dismiss
          </button>
        )}
      </div>
      {suggestions && suggestions.length > 0 && (
        <div className="suggestion-list">
          {suggestions.map((s, i) => (
            <div key={`${s.name}-${i}`} className="suggestion-card">
              <div className="suggestion-meta">
                <span className="line-swatch" style={{ backgroundColor: s.color }} />
                <span className="suggestion-name">{s.name}</span>
                <span className="muted">score {s.score}</span>
              </div>
              <button type="button" className="toolbar-button primary small" onClick={() => onAcceptSuggestion(s)}>
                Add
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Import / Export ──────────────────────────────────────────────────────────

function ImportExportPanel({ model, dispatch }: PanelSectionProps) {
  const fileRef = React.useRef<HTMLInputElement>(null);

  function handleExport() {
    const data = model.lines.map((l) => ({
      name: l.name,
      color: l.color,
      points: { a: l.points[0], b: l.points[l.points.length - 1] },
    }));
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'counting-lines.json';
    a.click();
    URL.revokeObjectURL(url);
  }

  function handleImport(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      try {
        const imported = JSON.parse(reader.result as string) as Array<{
          name: string;
          color: string;
          points: { a: [number, number]; b: [number, number] };
        }>;
        for (const item of imported) {
          const id = typeof crypto !== 'undefined' && crypto.randomUUID
            ? crypto.randomUUID()
            : `line-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
          dispatch({
            type: 'add-line',
            line: {
              id,
              name: item.name ?? 'line',
              color: item.color ?? '#e24b4a',
              kind: 'line',
              points: [item.points.a, item.points.b],
            },
          });
        }
      } catch {
        alert('Could not parse line config JSON.');
      }
    };
    reader.readAsText(file);
    e.target.value = '';
  }

  return (
    <div className="panel-card">
      <h2>Import / Export</h2>
      <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
        <button type="button" className="toolbar-button small" onClick={handleExport}
          disabled={model.lines.length === 0}>
          📤 Export JSON
        </button>
        <button type="button" className="toolbar-button small" onClick={() => fileRef.current?.click()}>
          📥 Import JSON
        </button>
        <input ref={fileRef} type="file" accept=".json" style={{ display: 'none' }} onChange={handleImport} />
      </div>
    </div>
  );
}

// ── Direction rose ────────────────────────────────────────────────────────────

function RosePanel({ trackStats }: PanelSectionProps) {
  if (!trackStats) return null;
  return (
    <div className="panel-card">
      <h2>Direction rose</h2>
      <RoseDiagram bins={trackStats.direction_bins} />
    </div>
  );
}

// ── Vehicle classes ───────────────────────────────────────────────────────────

function ClassBarsPanel({ trackStats }: PanelSectionProps) {
  if (!trackStats?.by_class || Object.keys(trackStats.by_class).length === 0) return null;
  const total = Object.values(trackStats.by_class).reduce((a, b) => a + b, 0) || 1;
  return (
    <div className="panel-card">
      <h2>Vehicle classes</h2>
      <div className="class-bars">
        {Object.entries(trackStats.by_class)
          .sort((a, b) => b[1] - a[1])
          .map(([cls, n]) => {
            const pct = Math.round((100 * n) / total);
            return (
              <div key={cls} className="class-bar-row">
                <span className="class-bar-label">{cls}</span>
                <div className="class-bar-track">
                  <div className="class-bar-fill" style={{ width: `${pct}%` }} />
                </div>
                <span className="class-bar-val">{n} ({pct}%)</span>
              </div>
            );
          })}
      </div>
    </div>
  );
}

// ── Registry ─────────────────────────────────────────────────────────────────

export const BUILTIN_PANELS: PanelSection[] = [
  { id: 'summary', order: 0, Component: SummaryPanel },
  { id: 'layers', order: 1, Component: LayersPanel },
  { id: 'color', order: 2, Component: DrawingColorPanel },
  { id: 'lines', order: 3, Component: LineListPanel },
  { id: 'suggest', order: 4, Component: SuggestPanel },
  { id: 'import-export', order: 5, Component: ImportExportPanel },
  { id: 'rose', order: 6, Component: RosePanel, shouldShow: ({ trackStats }) => !!trackStats },
  { id: 'classes', order: 7, Component: ClassBarsPanel,
    shouldShow: ({ trackStats }) => !!trackStats?.by_class && Object.keys(trackStats.by_class).length > 0 },
];
