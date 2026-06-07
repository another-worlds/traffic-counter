import React from 'react';
import {
  type TimestampApiConfig,
  type TimestampRegion,
  getTimestampRegionPreviewUrl,
} from './timestampApi';

type Props = {
  tsCfg: TimestampApiConfig;
  videoId: string;
  projectId: string;
  hasPreview: boolean;
  region?: TimestampRegion | null;
  cacheBust?: string | number;
};

function regionMethodLabel(method?: string): string {
  if (!method) return 'Timestamp region';
  if (method.startsWith('manual')) return 'Manual region';
  if (method.includes('ocr_timestamp')) return 'Auto-detected timestamp';
  if (method === 'heuristic') return 'Auto-detected (heuristic)';
  return `Auto-detected (${method})`;
}

export default function TimestampRegionPreview({
  tsCfg,
  videoId,
  projectId,
  hasPreview,
  region,
  cacheBust,
}: Props) {
  if (!hasPreview || !tsCfg.baseUrl) return null;

  const label = regionMethodLabel(region?.method);
  const dims = region
    ? `(${region.x}, ${region.y}) ${region.w}×${region.h}px`
    : null;
  const area = region?.area_percent != null ? `${region.area_percent}% of frame` : null;

  return (
    <div className="timestamp-region-block">
      <div className="timestamp-region-preview-wrap">
        <img
          className="timestamp-region-preview"
          src={getTimestampRegionPreviewUrl(tsCfg, videoId, projectId, cacheBust)}
          alt="Timestamp OCR crop"
        />
      </div>
      <p className="muted timestamp-region-note">
        {label}
        {dims ? ` · ${dims}` : ''}
        {area ? ` · ${area}` : ''}
        {' · OCR uses this crop only'}
      </p>
    </div>
  );
}