import React from 'react';
import type { SceneFrame, VideoSize } from './viewportState';
import { resolveApiBaseUrl, startExport, getExportStatus, downloadExportBlob } from './api';
import TimestampRegionEditor from './TimestampRegionEditor';
import TimestampRegionPreview from './TimestampRegionPreview';
import {
  type CorrectedCountsResponse,
  type GapInterval,
  type TimestampApiConfig,
  type TimestampMap,
  type TimestampStatus,
  GAP_REASON_LABELS,
  normalizeClockHourRows,
  normalizeIdealDayHourRows,
  isClockHourPresenceModel,
  getCorrectedCounts,
  getTimestampMap,
  getTimestampStatus,
  startTimestampScan,
  stopTimestampScan,
} from './timestampApi';
import {
  PresenceRescanNotice,
  CountsCorrectionStatus,
  GapBreakdownPanel,
  HourPresenceTable,
  IdealDayHourBar,
  PresenceTrack,
  ScanProgressPanel,
  SummaryStatsRow,
  TimelineCoverageBar,
} from './TimestampViz';

type Props = {
  videoId: string;
  projectId: string;
  lineIds: string[];
  tsCfg: TimestampApiConfig;
  apiBaseUrl?: string | null;
  frames?: SceneFrame[];
  frameIndex?: number;
  videoSize?: VideoSize;
  videoStatus?: string;
};

function fmtDuration(s: number): string {
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = Math.floor(s % 60);
  if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`;
  return `${m}:${String(sec).padStart(2, '0')}`;
}

function statusLabel(status: string): string {
  switch (status) {
    case 'done': return 'Complete';
    case 'processing': return 'Analyzing…';
    case 'error': return 'Error';
    case 'pending': return 'Queued / not scanned';
    case 'cancelled': return 'Stopped';
    default: return status;
  }
}

export default function TimestampPanel({
  videoId,
  projectId,
  lineIds,
  tsCfg,
  apiBaseUrl,
  frames = [],
  frameIndex = 0,
  videoSize = { width: 1920, height: 1080 },
  videoStatus = 'analyzed',
}: Props) {
  const [status, setStatus] = React.useState<TimestampStatus | null>(null);
  const [map, setMap] = React.useState<TimestampMap | null>(null);
  const [corrected, setCorrected] = React.useState<CorrectedCountsResponse | null>(null);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [showGaps, setShowGaps] = React.useState(false);
  const [regionEditorOpen, setRegionEditorOpen] = React.useState(false);
  const [exportBusy, setExportBusy] = React.useState(false);
  const [hoverTime, setHoverTime] = React.useState<number | null>(null);
  const [previewEpoch, setPreviewEpoch] = React.useState(0);

  const pollRef = React.useRef<ReturnType<typeof setInterval> | null>(null);
  const exportPollRef = React.useRef<ReturnType<typeof setInterval> | null>(null);
  const statusRef = React.useRef<string>('pending');
  const mapLoadedRef = React.useRef(false);
  const lineIdsRef = React.useRef(lineIds);
  lineIdsRef.current = lineIds;

  const clearPoll = React.useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const loadMapOnce = React.useCallback(async () => {
    if (!tsCfg.baseUrl || mapLoadedRef.current) return;
    const m = await getTimestampMap(tsCfg, videoId, projectId);
    setMap(m);
    mapLoadedRef.current = true;
    if (lineIdsRef.current.length > 0) {
      try {
        const cc = await getCorrectedCounts(tsCfg, videoId, lineIdsRef.current, projectId);
        setCorrected(cc);
      } catch {
        /* counts are best-effort */
      }
    }
  }, [tsCfg, videoId, projectId]);

  const pollStatus = React.useCallback(async () => {
    if (!tsCfg.baseUrl || !videoId || !projectId) return;
    try {
      const st = await getTimestampStatus(tsCfg, videoId, projectId);
      const prev = statusRef.current;
      statusRef.current = st.status;
      setStatus(st);

      if (st.status === 'done') {
        if (!mapLoadedRef.current || prev === 'processing') {
          mapLoadedRef.current = false;
          await loadMapOnce();
        }
        setError(null);
        if (prev === 'processing') {
          clearPoll();
        }
      } else if (st.status === 'error') {
        setError(st.error_message ?? 'Timestamp scan failed');
        clearPoll();
      } else if (st.status === 'processing') {
        setError(null);
      }
    } catch (err) {
      if (statusRef.current !== 'processing') {
        setError(err instanceof Error ? err.message : String(err));
      }
    }
  }, [tsCfg, videoId, projectId, loadMapOnce, clearPoll]);

  const startPolling = React.useCallback((intervalMs: number) => {
    clearPoll();
    pollStatus();
    pollRef.current = setInterval(pollStatus, intervalMs);
  }, [clearPoll, pollStatus]);

  // Reset only when the video changes — never when status updates.
  React.useEffect(() => {
    clearPoll();
    statusRef.current = 'pending';
    mapLoadedRef.current = false;
    setStatus(null);
    setMap(null);
    setCorrected(null);
    setError(null);
    setShowGaps(false);
    setRegionEditorOpen(false);
    setPreviewEpoch(0);

    if (!tsCfg.baseUrl || !videoId || !projectId) return;

    startPolling(5000);
    return clearPoll;
  }, [videoId, projectId, tsCfg.baseUrl, clearPoll, startPolling]);

  // Faster poll while processing only.
  React.useEffect(() => {
    if (status?.status === 'processing') {
      startPolling(4000);
    }
  }, [status?.status, startPolling]);

  // Refresh corrected counts when lines change on an already-loaded map.
  React.useEffect(() => {
    if (!mapLoadedRef.current || statusRef.current !== 'done' || lineIds.length === 0) return;
    getCorrectedCounts(tsCfg, videoId, lineIds, projectId)
      .then(setCorrected)
      .catch(() => { /* best-effort */ });
  }, [lineIds.join(','), tsCfg, videoId, projectId]);

  React.useEffect(() => () => {
    if (exportPollRef.current) clearInterval(exportPollRef.current);
    clearPoll();
  }, [clearPoll]);

  async function handleCorrectedExport() {
    const cfg = { baseUrl: resolveApiBaseUrl(apiBaseUrl) };
    if (!cfg.baseUrl || lineIds.length === 0) return;
    setExportBusy(true);
    setError(null);
    try {
      const { job_id } = await startExport(cfg, videoId, lineIds, true);
      if (exportPollRef.current) clearInterval(exportPollRef.current);
      exportPollRef.current = setInterval(async () => {
        try {
          const expStatus = await getExportStatus(cfg, job_id);
          if (expStatus.status === 'done') {
            clearInterval(exportPollRef.current!);
            const blob = await downloadExportBlob(cfg, job_id);
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = expStatus.filename ?? 'counts-corrected.xlsx';
            a.click();
            URL.revokeObjectURL(url);
            setExportBusy(false);
          } else if (expStatus.status === 'error') {
            clearInterval(exportPollRef.current!);
            setError(expStatus.error ?? 'Export failed');
            setExportBusy(false);
          }
        } catch (err) {
          clearInterval(exportPollRef.current!);
          setError(err instanceof Error ? err.message : String(err));
          setExportBusy(false);
        }
      }, 1000);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setExportBusy(false);
    }
  }

  async function handleStopScan() {
    if (!tsCfg.baseUrl) return;
    setBusy(true);
    setError(null);
    try {
      const res = await stopTimestampScan(tsCfg, videoId, projectId);
      clearPoll();
      statusRef.current = 'cancelled';
      setStatus({
        status: 'cancelled',
        auto_scan_enabled: false,
        error_message: res.status?.error_message ?? 'Stopped — set region manually',
        progress: res.status?.progress ?? {
          phase: 'cancelled',
          phase_label: 'Stopped',
          percent: 0,
          message: 'Auto-scan disabled',
        },
      });
      setRegionEditorOpen(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function handleScan(force = false) {
    if (!tsCfg.baseUrl) return;
    setBusy(true);
    setError(null);
    if (force) {
      mapLoadedRef.current = false;
      setMap(null);
      setCorrected(null);
    }
    try {
      await startTimestampScan(tsCfg, videoId, projectId, force);
      statusRef.current = 'processing';
      setStatus({
        status: 'processing',
        progress: { phase: 'opening', phase_label: 'Opening video', percent: 0 },
      });
      startPolling(3000);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  const isAnalyzed = videoStatus === 'analyzed';
  const isDone = status?.status === 'done';
  const isScanning = status?.status === 'processing';
  const isQueued = status?.status === 'pending';
  const canStopScan = isScanning || isQueued;
  const isProcessing = isScanning || busy;
  const autoScanOff = status?.auto_scan_enabled === false;
  const showResults = isDone && map != null;
  const idealDay = map?.ideal_day ?? map?.timeline_viz?.ideal_day ?? null;
  const presenceMapEnabled = Boolean(
    map?.stats?.hour_presence_map_enabled
    ?? map?.stats?.coherence_map_enabled
    ?? map?.timeline_viz?.hour_presence_map_enabled
    ?? map?.timeline_viz?.coherence_map_enabled,
  );
  const isClockHourModel = isClockHourPresenceModel(map?.stats);
  const hourPresence = isClockHourModel
    ? normalizeClockHourRows(
      map?.hour_presence ?? map?.timeline_viz?.hour_presence,
    )
    : [];
  const idealDayHours = normalizeIdealDayHourRows(
    map?.ideal_day_hours
    ?? map?.timeline_viz?.ideal_day_hours
    ?? (!isClockHourModel
      ? (map?.hour_presence
        ?? map?.timeline_viz?.hour_presence
        ?? map?.hour_coherence
        ?? map?.timeline_viz?.hour_coherence)
      : null),
  );
  const hasClockHourPresence = isClockHourModel && hourPresence.length > 0;
  const hasIdealDayBar = idealDayHours.length > 0;
  const showProgress = isProcessing || isDone;
  const hasRegionPreview = Boolean(status?.artifacts?.region_preview);
  const previewCacheBust = previewEpoch
    || status?.completed_at
    || (status?.progress ? `${status.progress.phase}-${status.progress.percent}` : undefined);
  const regionMeta = status?.region ?? map?.region ?? null;

  async function handleRegionSaved() {
    setPreviewEpoch((n) => n + 1);
    await pollStatus();
  }

  return (
    <div className="timestamp-panel">
      <div className="timestamp-panel-header">
        <div>
          <h3>Timestamp analysis</h3>
          <p className="muted">
            Reports clock-hour OSD completeness (parsed minutes per hour) and ideal-day footage placement.
          </p>
        </div>
        <div className="timestamp-actions">
          <span className={`timestamp-status-pill status-${status?.status ?? 'pending'}`}>
            {statusLabel(status?.status ?? 'pending')}
          </span>
          {canStopScan && (
            <button
              type="button"
              className="toolbar-button small danger"
              disabled={busy || !tsCfg.baseUrl}
              onClick={handleStopScan}
            >
              ⏹ Stop analysis
            </button>
          )}
          <button
            type="button"
            className={`toolbar-button small${regionEditorOpen ? ' active' : ''}`}
            disabled={!tsCfg.baseUrl || (isScanning && !autoScanOff)}
            onClick={() => setRegionEditorOpen((v) => !v)}
          >
            {regionEditorOpen ? '✕ Close region selector' : '🎯 Set timestamp region'}
          </button>
          <button
            type="button"
            className="toolbar-button small primary"
            disabled={isScanning || busy || !tsCfg.baseUrl || !isAnalyzed}
            title={isAnalyzed ? undefined : 'Vehicle analysis must complete before timestamp scan'}
            onClick={() => handleScan(isDone)}
          >
            {isScanning ? '⏳ Scanning…' : isDone ? '🔄 Re-scan' : '▶ Run scan'}
          </button>
        </div>
      </div>

      {!tsCfg.baseUrl && (
        <p className="timestamp-warn">Timestamp API unreachable (port 8200).</p>
      )}

      {error && (
        <p className="timestamp-error">{error.slice(0, 300)}</p>
      )}

      {!isAnalyzed && (
        <p className="muted">
          Video not analyzed yet — you can set the timestamp region now; run scan after vehicle tracking completes.
        </p>
      )}

      {autoScanOff && !isScanning && (
        <p className="muted">Auto-scan is off for this video — set the region manually, then run scan.</p>
      )}

      {regionEditorOpen && (
        <TimestampRegionEditor
          videoId={videoId}
          projectId={projectId}
          tsCfg={tsCfg}
          frames={frames}
          frameIndex={frameIndex}
          videoSize={videoSize}
          disabled={isScanning}
          onRegionSaved={handleRegionSaved}
        />
      )}

      {hasRegionPreview && (
        <TimestampRegionPreview
          tsCfg={tsCfg}
          videoId={videoId}
          projectId={projectId}
          hasPreview={hasRegionPreview}
          region={regionMeta}
          cacheBust={previewCacheBust}
        />
      )}

      {showProgress && (
        <ScanProgressPanel
          progress={status?.progress}
          status={status?.status ?? 'pending'}
          startedAt={status?.started_at}
          onForceRestart={isProcessing ? () => handleScan(true) : undefined}
        />
      )}

      {showResults && (
        <>
          <SummaryStatsRow map={map} corrected={corrected} />

          {hasIdealDayBar && (
            <IdealDayHourBar hours={idealDayHours} idealDay={idealDay} />
          )}
          {hasClockHourPresence ? (
            <HourPresenceTable hours={hourPresence} />
          ) : presenceMapEnabled ? (
            <PresenceRescanNotice />
          ) : null}

          <GapBreakdownPanel viz={map.timeline_viz} stats={map.stats} />

          {!presenceMapEnabled && (
            <>
              <TimelineCoverageBar
                viz={map.timeline_viz}
                gaps={map.gaps}
                onHoverTime={setHoverTime}
              />
              {hoverTime != null && (
                <p className="muted ts-hover-time">Hover: {fmtDuration(hoverTime)}</p>
              )}
              <PresenceTrack viz={map.timeline_viz} />
            </>
          )}

          <CountsCorrectionStatus
            corrected={corrected}
            lineCount={lineIds.length}
            hasGapMap
          />

          {lineIds.length > 0 && (
            <div className="timestamp-export-row">
              <button
                type="button"
                className="toolbar-button small primary"
                disabled={exportBusy}
                onClick={handleCorrectedExport}
              >
                {exportBusy ? '⏳ Building export…' : '📊 Export corrected XLSX'}
              </button>
              <span className="muted">
                Hour presence sheet · unparsed intervals · raw vs corrected
              </span>
            </div>
          )}

          {map.gaps.length > 0 && (
            <div className="timestamp-gaps-section">
              <button
                type="button"
                className="timestamp-gaps-toggle"
                onClick={() => setShowGaps((v) => !v)}
              >
                {showGaps ? '▾' : '▸'} Fragment list ({map.gaps.length})
              </button>
              {showGaps && (
                <div className="timestamp-gaps-list">
                  {map.gaps.slice(0, 50).map((g: GapInterval, i: number) => (
                    <div key={`${g.start_frame}-${i}`} className="timestamp-gap-row">
                      <span className="gap-time">
                        {fmtDuration(g.start_t_s)} – {fmtDuration(g.end_t_s)}
                      </span>
                      <span className="gap-reason">
                        {GAP_REASON_LABELS[g.reason] ?? g.reason}
                      </span>
                      <span className="gap-frames muted">
                        frames {g.start_frame}–{g.end_frame}
                      </span>
                    </div>
                  ))}
                  {map.gaps.length > 50 && (
                    <p className="muted">…and {map.gaps.length - 50} more</p>
                  )}
                </div>
              )}
            </div>
          )}
        </>
      )}

      {isQueued && !isProcessing && !autoScanOff && (
        <p className="muted">
          Waiting in the auto-scan queue — use Stop analysis to draw the region manually instead.
        </p>
      )}
    </div>
  );
}