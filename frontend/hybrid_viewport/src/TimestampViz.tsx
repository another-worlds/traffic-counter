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
  timestamp_drift: '#f59e0b',
  gap: '#fb7185',
};

const SCAN_PHASES = [
  { id: 'opening', label: 'Open' },
  { id: 'locating', label: 'Region' },
  { id: 'ocr', label: 'OCR' },
  { id: 'gaps', label: 'Hours' },
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
            {kind === 'valid' ? 'Parsed' : (GAP_REASON_LABELS[kind] ?? kind)}
          </span>
        ))}
      </div>
      {(gaps?.length ?? 0) > 0 && (
        <p className="muted ts-viz-caption">
          {gaps!.length} unparsed fragment{gaps!.length === 1 ? '' : 's'} highlighted on the bar above.
        </p>
      )}
    </div>
  );
}

export function PresenceTrack({ viz }: { viz?: TimelineViz | null }) {
  const total = viz?.total_duration_s ?? 0;
  const points = viz?.presence ?? [];
  if (total <= 0 || points.length === 0) return null;

  const presentPct = points.filter((p) => p.present).length / points.length;

  return (
    <div className="ts-viz-block">
      <div className="ts-viz-header">
        <h4>OSD presence</h4>
        <span className="muted">{(presentPct * 100).toFixed(0)}% parsed along timeline</span>
      </div>
      <div className="ts-presence-bar">
        {points.map((p, i) => {
          const tNext = points[i + 1]?.t_s ?? total;
          const { left, width } = pctOf(p.t_s, tNext, total);
          let color = SEGMENT_COLORS.valid;
          if (!p.present) color = '#475569';
          return (
            <div
              key={`${p.t_s}-${i}`}
              className="ts-presence-cell"
              style={{ left: `${left}%`, width: `${width}%`, background: color }}
              title={`${fmtDuration(p.t_s)} · ${p.present ? 'OSD parsed' : 'OSD missing'}`}
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
      </div>
    </div>
  );
}

export const PresenceCoherenceTrack = PresenceTrack;

const GAP_REASON_PRIORITY: Record<string, number> = {
  missing_osd: 5,
  time_reverse: 4,
  time_jump: 3,
  timestamp_drift: 3,
  frozen_osd: 2,
  sync_recovery: 1,
};

function primaryGapReason(reason: string): string {
  let best = 'sync_recovery';
  let bestPri = -1;
  for (const part of reason.split('+')) {
    const pri = GAP_REASON_PRIORITY[part] ?? 0;
    if (pri > bestPri) {
      bestPri = pri;
      best = part;
    }
  }
  return best;
}

function normalizeGapBreakdown(breakdown: Record<string, number>): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const [reason, count] of Object.entries(breakdown)) {
    if (count <= 0) continue;
    const primary = primaryGapReason(reason);
    counts[primary] = (counts[primary] ?? 0) + count;
  }
  return counts;
}

function hourBarColor(parsedPct: number, sampled: number): string {
  if (sampled <= 0) return '#334155';
  if (parsedPct >= 80) return '#2dd4bf';
  if (parsedPct >= 50) return '#f59e0b';
  return '#ef4444';
}

function shortHourLabel(hourLabel: string): string {
  const interval = hourLabel.match(/^(\d{2}):00/);
  if (interval) return interval[1];
  const utc = hourLabel.match(/(\d{2}):00 UTC$/);
  if (utc) return utc[1];
  return hourLabel.slice(-8, -6) || '?';
}

export function IdealDayHourBar({
  hours,
  idealDay,
}: {
  hours?: Array<{
    hour_label: string;
    minutes_sampled: number;
    parsed_percent: number;
    coverage_percent: number;
  }> | null;
  idealDay?: Record<string, unknown> | null;
}) {
  if (!hours?.length) return null;

  return (
    <div className="ts-viz-block">
      <div className="ts-viz-header">
        <h4>Ideal day footage map</h4>
        {idealDay && (
          <span className="muted">
            {String(idealDay.date ?? '')} {String(idealDay.start ?? '00:00')}–{String(idealDay.end ?? '24:00')}
          </span>
        )}
      </div>
      <div className="ts-ideal-day-bar">
        {hours.map((h) => (
          <div
            key={h.hour_label}
            className="ts-ideal-day-cell"
            style={{ background: hourBarColor(h.parsed_percent, h.minutes_sampled) }}
            title={`${h.hour_label}\nFootage in hour ${h.coverage_percent.toFixed(0)}% · Parsed ${h.parsed_percent.toFixed(0)}%`}
          >
            <span className="ts-ideal-day-cell-label">{shortHourLabel(h.hour_label)}</span>
          </div>
        ))}
      </div>
      <div className="ts-legend compact">
        <span className="ts-legend-item">
          <span className="ts-legend-swatch" style={{ background: '#334155' }} />
          No footage
        </span>
        <span className="ts-legend-item">
          <span className="ts-legend-swatch" style={{ background: '#2dd4bf' }} />
          ≥80% parsed
        </span>
        <span className="ts-legend-item">
          <span className="ts-legend-swatch" style={{ background: '#f59e0b' }} />
          50–79%
        </span>
        <span className="ts-legend-item">
          <span className="ts-legend-swatch" style={{ background: '#ef4444' }} />
          &lt;50%
        </span>
      </div>
    </div>
  );
}

export function IdealDayHourTable({
  hours,
  idealDay,
}: {
  hours?: Array<{
    hour_label: string;
    minutes_sampled: number;
    minutes_present: number;
    coverage_percent: number;
    parsed_percent: number;
    parsed_of_ideal_hour_percent?: number;
  }> | null;
  idealDay?: Record<string, unknown> | null;
}) {
  if (!hours?.length) return null;

  return (
    <div className="ts-hour-presence ts-ideal-day-hours">
      <h4>Ideal day hour presence</h4>
      {idealDay && (
        <p className="muted ts-hour-presence-caption ts-ideal-day-hours-caption">
          Ideal day {String(idealDay.date ?? '')} {String(idealDay.start ?? '00:00')}–{String(idealDay.end ?? '24:00')}
        </p>
      )}
      <table className="ts-hour-presence-table ts-ideal-day-hours-table">
        <thead>
          <tr>
            <th className="ts-ideal-day-col-light">Hour (UTC)</th>
            <th className="ts-ideal-day-col-light">Footage in hour</th>
            <th>Parsed</th>
            <th>% parsed</th>
            <th className="ts-ideal-day-col-light">Ideal hour fill</th>
          </tr>
        </thead>
        <tbody>
          {hours.map((h) => (
            <tr
              key={h.hour_label}
              className={h.minutes_sampled === 0 ? 'ts-hour-empty' : ''}
            >
              <td className="ts-ideal-day-col-light">{h.hour_label}</td>
              <td className="ts-ideal-day-col-light">{h.minutes_sampled} min</td>
              <td className="muted">{h.minutes_present}/{h.minutes_sampled}</td>
              <td>
                <span
                  className={
                    h.parsed_percent >= 80
                      ? 'ts-hour-good'
                      : h.parsed_percent >= 50
                        ? 'ts-hour-warn'
                        : 'ts-hour-bad'
                  }
                >
                  {h.parsed_percent.toFixed(0)}%
                </span>
              </td>
              <td className="ts-ideal-day-col-light">
                {(h.parsed_of_ideal_hour_percent ?? h.coverage_percent).toFixed(0)}%
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function PresenceRescanNotice() {
  return (
    <div className="ts-rescan-notice">
      <strong>Clock-hour presence report not available</strong>
      <p className="muted">
        This scan predates the clock-hour report format. Re-run timestamp scan to generate
        per-hour parsed-minute completeness (e.g. 57/60).
      </p>
    </div>
  );
}

export const CoherenceRescanNotice = PresenceRescanNotice;

export function HourPresenceTable({
  hours,
}: {
  hours?: Array<{
    hour_label: string;
    minutes_present: number;
    minutes_ideal?: number;
    parsed_fraction?: string;
    parsed_percent: number;
  }> | null;
}) {
  if (!hours?.length) return null;

  return (
    <div className="ts-hour-presence ts-hour-coherence">
      <h4>Clock-hour OSD presence</h4>
      <p className="muted ts-hour-presence-caption ts-hour-coherence-caption">
        Distinct parsed minutes per clock hour (60 ideal slots per hour).
      </p>
      <table className="ts-hour-presence-table ts-hour-coherence-table">
        <thead>
          <tr>
            <th>Hour</th>
            <th>Parsed</th>
            <th>%</th>
          </tr>
        </thead>
        <tbody>
          {hours.map((h) => {
            const ideal = h.minutes_ideal ?? 60;
            const fraction = h.parsed_fraction ?? `${h.minutes_present}/${ideal}`;
            const empty = h.minutes_present === 0;
            return (
              <tr
                key={h.hour_label}
                className={empty ? 'ts-hour-empty' : ''}
              >
                <td>{h.hour_label}</td>
                <td className="ts-hour-fraction">{fraction}</td>
                <td>
                  <span
                    className={
                      h.parsed_percent >= 80
                        ? 'ts-hour-good'
                        : h.parsed_percent >= 50
                          ? 'ts-hour-warn'
                          : 'ts-hour-bad'
                    }
                  >
                    {h.parsed_percent.toFixed(0)}%
                  </span>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export const HourCoherenceTable = HourPresenceTable;

export function ClockHourVideoCoverageTable({
  hours,
}: {
  hours?: Array<{
    hour_label: string;
    video_duration_s: number;
    video_duration_label: string;
    fragment_count: number;
    fragments?: Array<{ start_t_s: number; end_t_s: number; duration_s: number }>;
  }> | null;
}) {
  if (!hours?.length) return null;

  return (
    <div className="ts-hour-presence ts-clock-hour-video">
      <h4>Clock-hour video coverage</h4>
      <p className="muted ts-hour-presence-caption ts-clock-hour-video-caption">
        Video time attributed to each parsed wall-clock hour (contiguous fragments, no minute dedup).
      </p>
      <table className="ts-hour-presence-table ts-clock-hour-video-table">
        <thead>
          <tr>
            <th>Hour</th>
            <th>Video time</th>
            <th>Fragments</th>
          </tr>
        </thead>
        <tbody>
          {hours.map((h) => {
            const empty = h.video_duration_s <= 0;
            const fragHint = h.fragments?.length
              ? h.fragments
                .map((f) => `${fmtDuration(f.start_t_s)}–${fmtDuration(f.end_t_s)}`)
                .join(', ')
              : '';
            return (
              <tr
                key={h.hour_label}
                className={empty ? 'ts-hour-empty' : ''}
                title={fragHint || undefined}
              >
                <td>{h.hour_label}</td>
                <td className="ts-hour-fraction">
                  {empty ? '—' : (h.video_duration_label || `${h.video_duration_s.toFixed(0)}s`)}
                </td>
                <td>{empty ? '—' : h.fragment_count}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function GapBreakdownPanel({ viz, stats }: { viz?: TimelineViz | null; stats?: Record<string, unknown> }) {
  const presenceEnabled = Boolean(
    stats?.hour_presence_map_enabled ?? stats?.coherence_map_enabled,
  );
  if (presenceEnabled) return null;
  const raw = viz?.gap_breakdown ?? (stats?.by_reason as Record<string, number> | undefined) ?? {};
  const entries = Object.entries(normalizeGapBreakdown(raw)).sort((a, b) => b[1] - a[1]);
  if (entries.length === 0) return null;

  return (
    <div className="ts-gap-breakdown">
      <h4>Gap breakdown</h4>
      <div className="ts-gap-breakdown-grid">
        {entries.map(([reason, count]) => (
          <div key={reason} className="ts-gap-breakdown-item">
            <span
              className="ts-legend-swatch"
              style={{ background: SEGMENT_COLORS[reason] ?? SEGMENT_COLORS.gap }}
            />
            <span className="ts-gap-breakdown-count">{count}</span>
            <span className="ts-gap-breakdown-label">{GAP_REASON_LABELS[reason] ?? reason}</span>
          </div>
        ))}
      </div>
    </div>
  );
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
  const presentFrac = Number(
    stats.parsed_fraction ?? map.timeline_summary?.present_fraction ?? 0,
  );
  const hoursWithCoverage = Number(stats.hours_with_coverage ?? 0);
  const unparsedFrac = Number(stats.gap_fraction ?? 0);
  const numSegments = Number(map.num_segments ?? stats.num_segments ?? 0);
  const syncEnabled = Boolean(stats.sync_map_enabled);
  const presenceEnabled = Boolean(
    stats.hour_presence_map_enabled ?? stats.coherence_map_enabled ?? syncEnabled,
  );

  return (
    <div className="timestamp-stats-grid">
      {syncEnabled && numSegments > 0 && !presenceEnabled && (
        <div className="timestamp-stat">
          <span className="timestamp-stat-val">{numSegments}</span>
          <span className="timestamp-stat-label">Trusted OSD segments</span>
        </div>
      )}
      {presenceEnabled && (
        <div className="timestamp-stat">
          <span className="timestamp-stat-val">{(presentFrac * 100).toFixed(0)}%</span>
          <span className="timestamp-stat-label">1-min bins parsed</span>
        </div>
      )}
      {presenceEnabled && hoursWithCoverage > 0 && (
        <div className="timestamp-stat">
          <span className="timestamp-stat-val">{hoursWithCoverage}</span>
          <span className="timestamp-stat-label">UTC hours with footage</span>
        </div>
      )}
      {presenceEnabled && (
        <div className="timestamp-stat">
          <span className="timestamp-stat-val">{(unparsedFrac * 100).toFixed(1)}%</span>
          <span className="timestamp-stat-label">Unparsed footage</span>
        </div>
      )}
      <div className="timestamp-stat">
        <span className="timestamp-stat-val">{(presentFrac * 100).toFixed(0)}%</span>
        <span className="timestamp-stat-label">OSD parsed (samples)</span>
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