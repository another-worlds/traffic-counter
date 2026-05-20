import type { ApiLine, CountsBundle, LineCount, Suggestion } from './viewportState';

export type ApiBaseConfig = { baseUrl: string };

// The bootstrap "hint" comes from PUBLIC_API_URL on the server, which is
// often "http://localhost:8000" for dev. When the user opens Streamlit on
// a remote host, that URL resolves to *the user's own machine*, where
// nothing is listening, and Chrome's Private-Network-Access policy blocks
// the loopback fetch anyway. Override to the iframe's own hostname.
export function resolveApiBaseUrl(hint?: string | null): string {
  const here = typeof window !== 'undefined' ? window.location : null;
  if (!hint) {
    if (!here) return '';
    return `${here.protocol}//${here.hostname}:8000`;
  }
  try {
    const u = new URL(hint);
    const hintIsLocal = u.hostname === 'localhost' || u.hostname === '127.0.0.1';
    const browserIsLocal =
      !!here && (here.hostname === 'localhost' || here.hostname === '127.0.0.1');
    if (hintIsLocal && here && !browserIsLocal) {
      return `${u.protocol}//${here.hostname}:${u.port || '8000'}`;
    }
    return hint;
  } catch {
    return hint;
  }
}

export class ApiError extends Error {
  status: number;
  body: unknown;
  constructor(message: string, status: number, body: unknown) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

export type ApiLineCreatePayload = {
  name: string;
  color: string;
  points: { a: [number, number]; b: [number, number] };
};

export type ApiLineUpdatePayload = Partial<ApiLineCreatePayload>;

export type CountsApiResponse = {
  total_unique_tracks: number;
  sum_across_lines: number;
  per_line: LineCount[];
};

async function request<T>(
  cfg: ApiBaseConfig,
  path: string,
  init: RequestInit & { signal?: AbortSignal } = {},
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
    let body: unknown = null;
    try { body = await res.json(); } catch { /* swallow */ }
    throw new ApiError(`${init.method ?? 'GET'} ${path} → ${res.status}`, res.status, body);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export function listLines(cfg: ApiBaseConfig, videoId: string, signal?: AbortSignal) {
  return request<ApiLine[]>(cfg, `/videos/${encodeURIComponent(videoId)}/lines`, { signal });
}

export function createLine(
  cfg: ApiBaseConfig,
  videoId: string,
  payload: ApiLineCreatePayload,
  signal?: AbortSignal,
) {
  return request<ApiLine>(
    cfg,
    `/videos/${encodeURIComponent(videoId)}/lines`,
    { method: 'POST', body: JSON.stringify(payload), signal },
  );
}

export function updateLine(
  cfg: ApiBaseConfig,
  lineId: string,
  payload: ApiLineUpdatePayload,
  signal?: AbortSignal,
) {
  return request<ApiLine>(
    cfg,
    `/lines/${encodeURIComponent(lineId)}`,
    { method: 'PATCH', body: JSON.stringify(payload), signal },
  );
}

export function deleteLine(cfg: ApiBaseConfig, lineId: string, signal?: AbortSignal) {
  return request<void>(
    cfg,
    `/lines/${encodeURIComponent(lineId)}`,
    { method: 'DELETE', signal },
  );
}

export function computeCounts(
  cfg: ApiBaseConfig,
  videoId: string,
  body: { line_ids: string[] },
  signal?: AbortSignal,
) {
  return request<CountsApiResponse>(
    cfg,
    `/videos/${encodeURIComponent(videoId)}/counts`,
    { method: 'POST', body: JSON.stringify(body), signal },
  );
}

export function requestSuggestions(
  cfg: ApiBaseConfig,
  videoId: string,
  body: { n: number },
  signal?: AbortSignal,
) {
  return request<Suggestion[]>(
    cfg,
    `/videos/${encodeURIComponent(videoId)}/suggest-lines`,
    { method: 'POST', body: JSON.stringify(body), signal },
  );
}

export function countsApiToBundle(resp: CountsApiResponse): CountsBundle {
  const perLine: Record<string, LineCount> = {};
  for (const row of resp.per_line || []) {
    perLine[String(row.line_id)] = row;
  }
  return {
    total_unique_tracks: Number(resp.total_unique_tracks || 0),
    per_line: perLine,
  };
}
