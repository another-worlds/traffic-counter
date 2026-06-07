import React from 'react';
import {
  GAP_REASON_LABELS,
  type CorrectedCountsResponse,
  type GapInterval,
  type TimestampMap,
  type TimestampProgress,
} from './timestampApi';

export const SEGMENT_COLORS: Record<string, string> = {
  valid: '#2dd4bf',
  missing_osd: '#ef4444',
  time_jump: '#f97316',
  time_reverse: '#a855f7',
  frozen_osd: '#eab308',
  sync_recovery: '#64748b',
  gap: '#fb7185',
};

const SCAN_PHASES = [
  { id: 'opening', label: 'Open' },
  { id: 'locating', label: 'Region' },
  { id: 'ocr', label: 'OCR' },
  { id: 'gaps', label: 'Gaps' },
  { id: 'finalize', label: 'Save' },
] as const;

function fmtDuration(s: number): string {
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = Math.floor(s % 60);
  if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`;
  return `${m}:${String(sec).padStart(2, '0')}`;
}

function pctOf(start: number, end: number, total: number): { left: number; width: number } {
  if (total <= 0) return { left: 0, width: 0 };
  const left = (start / total) * 100;
  const width = ((end - start) / total) * 100;
  return { left: Math.max(0, left), width: Math.max(0.15, width) };
}

type TimelineSegment = {
  start_t_s: number;
  end_t_s: number;
  kind: string;
  reason?: string;
};

type PresencePoint = {
  t_s: number;
  present: boolean;
  in_gap: boolean;
  gap_kind?: string | null;
};

type TimelineViz = {
  total_duration_s: number;
  total_frames?: number;
  valid_duration_s?: number;
  gap_duration_s?: number;
  segments: TimelineSegment[];
  presence: PresencePoint[];
  gap_breakdown?: Record<string, number>;
  num_gaps?: number;
};

export function ScanProgressPanel({
  progress,
  status,
  startedAt,
  onForceRestart,
}: {
  progress?: TimestampProgress | null;
  status: string;
  startedAt?: string | null;
  onForceRestart?: () => void;
}) {
  const pct = progress?.percent ?? (status === 'done' ? 100 : 0);
  const phase = progress?.phase ?? status;
  const phaseIdx = SCAN_PHASES.findIndex((p) => p.id === phase);

  const startedMs = startedAt ? Date.parse(startedAt) : NaN;
  const stale =
    status === 'processing'
    && Number.isFinite(startedMs)
    && Date.now() - startedMs > 45 * 60 * 1000;

  return (
    <div className="ts-progress-panel">
      <div className="ts-progress-header">
        <span className="ts-progress-label">
          {status === 'done' ? 'Scan complete' : progress?.phase_label ?? 'Preparing…'}
        </span>
        <span className="ts-progress-pct">{pct.toFixed(0)}%</span>
      </div>
      <div className="ts-progress-track" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
        <div className="ts-progress-fill" style={{ width: `${Math.min(100, pct)}%` }} />
      </div>
      {progress?.message && (
        <p className="muted ts-progress-message">{progress.message}</p>
      )}
      <div className="ts-phase-steps">
        {SCAN_PHASES.map((p, i) => {
          const done = status === 'done' || (phaseIdx >= 0 && i < phaseIdx) || (phaseIdx === i && pct >= 100);
          const active = phase === p.id && status === 'processing';
          return (
            <div
              key={p.id}
              className={`ts-phase-step${done ? ' done' : ''}${active ? ' active' : ''}`}
            >
              <span className="ts-phase-dot" />
              <span>{p.label}</span>
            </div>
          );
        })}
      </div>
      {stale && onForceRestart && (
        <div className="ts-stale-row">
          <span className="muted">Scan may be stuck.</span>
          <button type="button" className="toolbar-button small" onClick={onForceRestart}>
            Force restart
          </button>
        </div>
      )}
    </div>
  );
}

export function TimelineCoverageBar({
  viz,
  gaps,
  onHoverTime,
}: {
  viz?: TimelineViz | null;
  gaps?: GapInterval[];
  onHoverTime?: (t: number | null) => void;
}) {
  const total = viz?.total_duration_s ?? 0;
  const segments = viz?.segments ?? [];
  if (total <= 0) return null;

  return (
    <div className="ts-viz-block">
      <div className="ts-viz-header">
        <h4>Footage timeline</h4>
        <span className="muted">{fmtDuration(total)} total</span>
      </div>
      <div
        className="ts-coverage-bar"
        onMouseLeave={() => onHoverTime?.(null)}
      >
        {segments.map((seg, i) => {
          const { left, width } = pctOf(seg.start_t_s, seg.end_t_s, total);
          const color = SEGMENT_COLORS[seg.kind] ?? SEGMENT_COLORS.gap;
          const label = seg.kind === 'valid'
            ? `Valid ${fmtDuration(seg.start_t_s)}–${fmtDuration(seg.end_t_s)}`
            : `${GAP_REASON_LABELS[seg.kind] ?? seg.kind} ${fmtDuration(seg.start_t_s)}–${fmtDuration(seg.end_t_s)}`;
          return (
            <div
              key={`${seg.start_t_s}-${i}`}
              className="ts-coverage-seg"
              style={{ left: `${left}%`, width: `${width}%`, background: color }}
              title={label}
              onMouseEnter={() => onHoverTime?.((seg.start_t_s + seg.end_t_s) / 2)}
            />
          );
        })}
      </div>
      <div className="ts-legend">
        {Object.entries(SEGMENT_COLORS).filter(([k]) => k !== 'gap').map(([kind, color]) => (
          <span key={kind} className="ts-legend-item">
            <span className="ts-legend-swatch" style={{ background: color }} />
            {kind === 'valid' ? 'Coherent' : (GAP_REASON_LABELS[kind] ?? kind)}
          </span>
        ))}
      </div>
      {(gaps?.length ?? 0) > 0 && (
        <p className="muted ts-viz-caption">
          {gaps!.length} incoherent fragment{gaps!.length === 1 ? '' : 's'} highlighted on the bar above.
        </p>
      )}
    </div>
  );
}

export function PresenceCoherenceTrack({ viz }: { viz?: TimelineViz | null }) {
  const total = viz?.total_duration_s ?? 0;
  const points = viz?.presence ?? [];
  if (total <= 0 || points.length === 0) return null;

  const presentPct = points.filter((p) => p.present && !p.in_gap).length / points.length;

  return (
    <div className="ts-viz-block">
      <div className="ts-viz-header">
        <h4>OSD coherence map</h4>
        <span className="muted">{(presentPct * 100).toFixed(0)}% readable &amp; coherent</span>
      </div>
      <div className="ts-presence-bar">
        {points.map((p, i) => {
          const tNext = points[i + 1]?.t_s ?? total;
          const { left, width } = pctOf(p.t_s, tNext, total);
          let color = SEGMENT_COLORS.valid;
          if (!p.present) color = '#475569';
          else if (p.in_gap && p.gap_kind) color = SEGMENT_COLORS[p.gap_kind] ?? SEGMENT_COLORS.gap;
          return (
            <div
              key={`${p.t_s}-${i}`}
              className="ts-presence-cell"
              style={{ left: `${left}%`, width: `${width}%`, background: color }}
              title={`${fmtDuration(p.t_s)} · ${p.present ? 'OSD read' : 'OSD missing'}${p.in_gap ? ' · in gap' : ''}`}
            />
          );
        })}
      </div>
      <div className="ts-legend compact">
        <span className="ts-legend-item">
          <span className="ts-legend-swatch" style={{ background: SEGMENT_COLORS.valid }} />
          OSD OK
        </span>
        <span className="ts-legend-item">
          <span className="ts-legend-swatch" style={{ background: '#475569' }} />
          OSD missing
        </span>
        <span className="ts-legend-item">
          <span className="ts-legend-swatch" style={{ background: SEGMENT_COLORS.time_jump }} />
          Incoherent
        </span>
      </div>
    </div>
  );
}

export function GapBreakdownPanel({ viz, stats }: { viz?: TimelineViz | null; stats?: Record<string, unknown> }) {
  const breakdown = viz?.gap_breakdown ?? (stats?.by_reason as Record<string, number> | undefined) ?? {};
  const entries = Object.entries(breakdown).filter(([, n]) => n > 0);
  if (entries.length === 0) return null;

  return (
    <div className="ts-gap-breakdown">
      <h4>Fragment types</h4>
      <div className="ts-gap-breakdown-grid">
        {entries.map(([reason, count]) => (
          <div key={reason} className="ts-gap-breakdown-item">
            <span
              className="ts-legend-swatch"
              style={{ background: SEGMENT_COLORS[_gapKind(reason)] ?? SEGMENT_COLORS.gap }}
            />
            <span className="ts-gap-breakdown-count">{count}</span>
            <span className="ts-gap-breakdown-label">{GAP_REASON_LABELS[reason] ?? reason}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function _gapKind(reason: string): string {
  for (const k of Object.keys(GAP_REASON_LABELS)) {
    if (reason.includes(k)) return k;
  }
  return 'gap';
}

export function CountsCorrectionStatus({
  corrected,
  lineCount,
  hasGapMap,
}: {
  corrected: CorrectedCountsResponse | null;
  lineCount: number;
  hasGapMap: boolean;
}) {
  if (!hasGapMap) return null;

  const applied = corrected != null && lineCount > 0;
  const excluded = corrected?.rows_excluded ?? 0;
  const excludedFrac = corrected?.rows_excluded_fraction ?? 0;

  return (
    <div className="ts-counts-status">
      <h4>Counts correction status</h4>
      <div className="ts-counts-status-grid">
        <div className={`ts-counts-status-item${applied ? ' applied' : ''}`}>
          <span className="ts-counts-status-icon">{applied ? '✓' : '○'}</span>
          <div>
            <strong>{applied ? 'Applied to counts' : 'Not applied yet'}</strong>
            <p className="muted">
              {lineCount === 0
                ? 'Draw and save counting lines to see corrected totals.'
                : applied
                  ? `${excluded} track crossing(s) excluded (${(excludedFrac * 100).toFixed(1)}%) inside gap fragments.`
                  : 'Loading corrected counts…'}
            </p>
          </div>
        </div>
        {applied && corrected && (
          <div className="ts-counts-status-item">
            <span className="ts-counts-status-val">
              {corrected.delta_per_line.reduce((s, d) => s + d.excluded, 0)}
            </span>
            <span className="muted">detections in gaps</span>
          </div>
        )}
      </div>
      {applied && corrected.delta_per_line.length > 0 && (
        <table className="ts-counts-mini-table">
          <thead>
            <tr>
              <th>Line</th>
              <th>Raw</th>
              <th>Corrected</th>
              <th>In gaps</th>
            </tr>
          </thead>
          <tbody>
            {corrected.delta_per_line.map((d) => (
              <tr key={d.line_id}>
                <td>{d.line_name}</td>
                <td>{d.raw_total}</td>
                <td className="corrected-val">{d.corrected_total}</td>
                <td className={d.excluded > 0 ? 'excluded-val' : ''}>{d.excluded}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

export function SummaryStatsRow({
  map,
  corrected,
}: {
  map: TimestampMap;
  corrected: CorrectedCountsResponse | null;
}) {
  const stats = map.stats ?? {};
  const viz = map.timeline_viz;
  const numGaps = Number(viz?.num_gaps ?? stats.num_gaps ?? map.gaps?.length ?? 0);
  const gapFraction = Number(stats.gap_fraction ?? 0);
  const presentFrac = Number(map.timeline_summary?.present_fraction ?? 0);
  const numSegments = Number(map.num_segments ?? stats.num_segments ?? 0);
  const trustedFrac = Number(stats.trusted_fraction ?? 0);
  const syncEnabled = Boolean(stats.sync_map_enabled);

  return (
    <div className="timestamp-stats-grid">
      {syncEnabled && numSegments > 0 && (
        <div className="timestamp-stat">
          <span className="timestamp-stat-val">{numSegments}</span>
          <span className="timestamp-stat-label">Trusted OSD segments</span>
        </div>
      )}
      {syncEnabled && trustedFrac > 0 && (
        <div className="timestamp-stat">
          <span className="timestamp-stat-val">{(trustedFrac * 100).toFixed(0)}%</span>
          <span className="timestamp-stat-label">Trusted 1-min bins</span>
        </div>
      )}
      <div className="timestamp-stat">
        <span className="timestamp-stat-val">{numGaps}</span>
        <span className="timestamp-stat-label">Missing / incoherent fragments</span>
      </div>
      <div className="timestamp-stat">
        <span className="timestamp-stat-val">{(gapFraction * 100).toFixed(1)}%</span>
        <span className="timestamp-stat-label">Timeline in gaps</span>
      </div>
      <div className="timestamp-stat">
        <span className="timestamp-stat-val">{(presentFrac * 100).toFixed(0)}%</span>
        <span className="timestamp-stat-label">OSD readable</span>
      </div>
      <div className="timestamp-stat">
        <span className="timestamp-stat-val">
          {fmtDuration(Number(viz?.gap_duration_s ?? stats.gap_duration_s ?? 0))}
        </span>
        <span className="timestamp-stat-label">Gap duration</span>
      </div>
      {corrected && (
        <div className="timestamp-stat">
          <span className="timestamp-stat-val">
            {(corrected.rows_excluded_fraction * 100).toFixed(1)}%
          </span>
          <span className="timestamp-stat-label">Detections excluded</span>
        </div>
      )}
    </div>
  );
}