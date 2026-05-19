import type { ApiLine, CountsBundle, LineCount, Suggestion } from './viewportState';

export type ApiBaseConfig = { baseUrl: string };

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

export function listLines(cfg: ApiBaseConfig, projectId: string, signal?: AbortSignal) {
  return request<ApiLine[]>(cfg, `/projects/${encodeURIComponent(projectId)}/lines`, { signal });
}

export function createLine(
  cfg: ApiBaseConfig,
  projectId: string,
  payload: ApiLineCreatePayload,
  signal?: AbortSignal,
) {
  return request<ApiLine>(
    cfg,
    `/projects/${encodeURIComponent(projectId)}/lines`,
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
  projectId: string,
  body: { video_ids: string[]; line_ids: string[] },
  signal?: AbortSignal,
) {
  return request<CountsApiResponse>(
    cfg,
    `/projects/${encodeURIComponent(projectId)}/counts`,
    { method: 'POST', body: JSON.stringify(body), signal },
  );
}

export function requestSuggestions(
  cfg: ApiBaseConfig,
  projectId: string,
  body: { video_ids: string[]; n: number },
  signal?: AbortSignal,
) {
  return request<Suggestion[]>(
    cfg,
    `/projects/${encodeURIComponent(projectId)}/suggest-lines`,
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
