export type TimestampApiConfig = { baseUrl: string };

export type TimestampProgress = {
  phase: string;
  phase_label: string;
  percent: number;
  message?: string | null;
};

export type TimestampStatus = {
  status: string;
  artifacts?: {
    region?: boolean;
    region_preview?: boolean;
    timeline?: boolean;
    gaps?: boolean;
    sync_map?: boolean;
  };
  region?: TimestampRegion | null;
  error_message?: string | null;
  stats?: Record<string, unknown> | null;
  progress?: TimestampProgress | null;
  started_at?: string | null;
  completed_at?: string | null;
  auto_scan_enabled?: boolean;
  cancel_requested?: boolean;
};

export type GapInterval = {
  start_frame: number;
  end_frame: number;
  start_t_s: number;
  end_t_s: number;
  reason: string;
};

export type TimelineSegment = {
  start_t_s: number;
  end_t_s: number;
  kind: string;
  reason?: string;
};

export type TimelinePresencePoint = {
  t_s: number;
  present: boolean;
  in_gap: boolean;
  gap_kind?: string | null;
};

export type TimelineViz = {
  total_duration_s: number;
  total_frames?: number;
  valid_duration_s?: number;
  gap_duration_s?: number;
  segments: TimelineSegment[];
  presence: TimelinePresencePoint[];
  gap_breakdown?: Record<string, number>;
  num_gaps?: number;
};

export type TimestampRegion = {
  x: number;
  y: number;
  w: number;
  h: number;
  confidence?: number;
  method?: string;
  video_width?: number;
  video_height?: number;
  area_percent?: number;
  source_frame_index?: number;
  processing_mode?: string;
};

export type TimestampRegionInput = {
  x: number;
  y: number;
  w: number;
  h: number;
  source_frame_index?: number;
  video_width?: number;
  video_height?: number;
};

export type TimestampMap = {
  region?: {
    x: number;
    y: number;
    w: number;
    h: number;
    confidence?: number;
    method?: string;
    area_percent?: number;
    processing_mode?: string;
    video_width?: number;
    video_height?: number;
  } | null;
  gaps: GapInterval[];
  stats: Record<string, unknown>;
  timeline_summary?: Record<string, unknown> | null;
  timeline_viz?: TimelineViz | null;
  wall_clock_buckets?: Array<Record<string, unknown>> | null;
  num_segments?: number | null;
};

export type LineDelta = {
  line_id: string;
  line_name: string;
  raw_total: number;
  corrected_total: number;
  excluded: number;
};

export type CorrectedCountsResponse = {
  gap_stats: Record<string, unknown>;
  rows_excluded: number;
  rows_excluded_fraction: number;
  raw_counts: { per_line: Array<{ line_id: string; total: number }> };
  corrected_counts: { per_line: Array<{ line_id: string; total: number }> };
  delta_per_line: LineDelta[];
};

export function resolveTimestampApiBaseUrl(hint?: string | null): string {
  const here = typeof window !== 'undefined' ? window.location : null;
  if (!hint) {
    if (!here) return '';
    return `${here.protocol}//${here.hostname}:8200`;
  }
  try {
    const u = new URL(hint);
    const hintIsLocal = u.hostname === 'localhost' || u.hostname === '127.0.0.1';
    const browserIsLocal =
      !!here && (here.hostname === 'localhost' || here.hostname === '127.0.0.1');
    if (hintIsLocal && here && !browserIsLocal) {
      return `${u.protocol}//${here.hostname}:8200`;
    }
    return hint;
  } catch {
    return hint;
  }
}

function withProjectId(path: string, projectId?: string): string {
  if (!projectId) return path;
  const sep = path.includes('?') ? '&' : '?';
  return `${path}${sep}project_id=${encodeURIComponent(projectId)}`;
}

async function tsRequest<T>(
  cfg: TimestampApiConfig,
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const url = `${cfg.baseUrl.replace(/\/$/, '')}${path}`;
  const res = await fetch(url, {
    ...init,
    headers: {
      Accept: 'application/json',
      ...(init.body ? { 'Content-Type': 'application/json' } : {}),
      ...(init.headers || {}),
    },
  });
  if (!res.ok) {
    let detail = '';
    try {
      const body = await res.json();
      const raw = body.detail ?? body;
      if (Array.isArray(raw)) {
        detail = raw
          .map((item: { loc?: unknown[]; msg?: string }) => {
            const field = Array.isArray(item.loc) ? item.loc.filter((p) => p !== 'body').join('.') : '';
            return field ? `${field}: ${item.msg ?? ''}` : (item.msg ?? JSON.stringify(item));
          })
          .join('; ');
      } else if (typeof raw === 'object' && raw !== null) {
        detail = JSON.stringify(raw);
      } else {
        detail = String(raw);
      }
    } catch {
      detail = await res.text();
    }
    throw new Error(`${init.method ?? 'GET'} ${path} → ${res.status}: ${detail}`);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export function getTimestampStatus(
  cfg: TimestampApiConfig,
  videoId: string,
  projectId?: string,
) {
  return tsRequest<TimestampStatus>(
    cfg,
    withProjectId(`/videos/${encodeURIComponent(videoId)}/timestamp-status`, projectId),
  );
}

export function getSourcePreviewFrameUrl(
  cfg: TimestampApiConfig,
  videoId: string,
  projectId?: string,
  cacheBust?: string | number,
): string {
  const base = `${cfg.baseUrl.replace(/\/$/, '')}${withProjectId(
    `/videos/${encodeURIComponent(videoId)}/source-preview-frame`,
    projectId,
  )}`;
  if (cacheBust == null) return base;
  const sep = base.includes('?') ? '&' : '?';
  return `${base}${sep}v=${encodeURIComponent(String(cacheBust))}`;
}

export function getTimestampRegionPreviewUrl(
  cfg: TimestampApiConfig,
  videoId: string,
  projectId?: string,
  cacheBust?: string | number,
): string {
  const base = `${cfg.baseUrl.replace(/\/$/, '')}${withProjectId(
    `/videos/${encodeURIComponent(videoId)}/timestamp-region-preview`,
    projectId,
  )}`;
  if (cacheBust == null) return base;
  const sep = base.includes('?') ? '&' : '?';
  return `${base}${sep}v=${encodeURIComponent(String(cacheBust))}`;
}

export function getTimestampMap(
  cfg: TimestampApiConfig,
  videoId: string,
  projectId?: string,
) {
  return tsRequest<TimestampMap>(
    cfg,
    withProjectId(`/videos/${encodeURIComponent(videoId)}/timestamp-map`, projectId),
  );
}

export function getTimestampRegion(
  cfg: TimestampApiConfig,
  videoId: string,
  projectId?: string,
) {
  return tsRequest<TimestampRegion>(
    cfg,
    withProjectId(`/videos/${encodeURIComponent(videoId)}/timestamp-region`, projectId),
  );
}

export function putTimestampRegion(
  cfg: TimestampApiConfig,
  videoId: string,
  projectId: string | undefined,
  region: TimestampRegionInput,
) {
  return tsRequest<TimestampRegion>(
    cfg,
    withProjectId(`/videos/${encodeURIComponent(videoId)}/timestamp-region`, projectId),
    { method: 'PUT', body: JSON.stringify(region) },
  );
}

export function deleteTimestampRegion(
  cfg: TimestampApiConfig,
  videoId: string,
  projectId?: string,
) {
  return tsRequest<void>(
    cfg,
    withProjectId(`/videos/${encodeURIComponent(videoId)}/timestamp-region`, projectId),
    { method: 'DELETE' },
  );
}

export function stopTimestampScan(
  cfg: TimestampApiConfig,
  videoId: string,
  projectId?: string,
) {
  return tsRequest<{ stopped: boolean; status: TimestampStatus }>(
    cfg,
    withProjectId(`/videos/${encodeURIComponent(videoId)}/timestamp-scan/stop`, projectId),
    { method: 'POST', body: '{}' },
  );
}

export function startTimestampScan(
  cfg: TimestampApiConfig,
  videoId: string,
  projectId?: string,
  force = false,
  region?: TimestampRegionInput,
) {
  return tsRequest<{ queued: boolean; message?: string }>(
    cfg,
    withProjectId(`/videos/${encodeURIComponent(videoId)}/timestamp-scan`, projectId),
    { method: 'POST', body: JSON.stringify({ force, region }) },
  );
}

export function getCorrectedCounts(
  cfg: TimestampApiConfig,
  videoId: string,
  lineIds: string[],
  projectId?: string,
) {
  return tsRequest<CorrectedCountsResponse>(
    cfg,
    withProjectId(`/videos/${encodeURIComponent(videoId)}/corrected-counts`, projectId),
    { method: 'POST', body: JSON.stringify({ line_ids: lineIds }) },
  );
}

export const GAP_REASON_LABELS: Record<string, string> = {
  missing_osd: 'Missing OSD',
  time_jump: 'Time jump',
  time_reverse: 'Time reverse',
  frozen_osd: 'Frozen timestamp',
  sync_recovery: 'Stabilizing after join',
};