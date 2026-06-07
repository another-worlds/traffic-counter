import React from 'react';
import type { SceneFrame, VideoSize } from './viewportState';
import {
  type TimestampApiConfig,
  type TimestampRegion,
  deleteTimestampRegion,
  getSourcePreviewFrameUrl,
  getTimestampRegion,
  getTimestampRegionPreviewUrl,
  putTimestampRegion,
} from './timestampApi';

type Rect = { x: number; y: number; w: number; h: number };

type Props = {
  videoId: string;
  projectId: string;
  tsCfg: TimestampApiConfig;
  frames: SceneFrame[];
  frameIndex: number;
  videoSize: VideoSize;
  disabled?: boolean;
  onRegionSaved?: () => void;
};

const MIN_W = 20;
const MIN_H = 10;
const HANDLE_R = 6;

function clampRect(r: Rect, maxW: number, maxH: number): Rect {
  const x = Math.max(0, Math.min(r.x, maxW - MIN_W));
  const y = Math.max(0, Math.min(r.y, maxH - MIN_H));
  const w = Math.max(MIN_W, Math.min(r.w, maxW - x));
  const h = Math.max(MIN_H, Math.min(r.h, maxH - y));
  return { x, y, w, h };
}

function screenToSvg(svg: SVGSVGElement, clientX: number, clientY: number): [number, number] {
  const pt = svg.createSVGPoint();
  pt.x = clientX;
  pt.y = clientY;
  const ctm = svg.getScreenCTM();
  if (!ctm) return [0, 0];
  const t = pt.matrixTransform(ctm.inverse());
  return [t.x, t.y];
}

export default function TimestampRegionEditor({
  videoId,
  projectId,
  tsCfg,
  frames,
  frameIndex,
  videoSize,
  disabled,
  onRegionSaved,
}: Props) {
  const [draft, setDraft] = React.useState<Rect | null>(null);
  const [saved, setSaved] = React.useState<TimestampRegion | null>(null);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [previewKey, setPreviewKey] = React.useState(0);
  const [effectiveSize, setEffectiveSize] = React.useState<VideoSize>(videoSize);

  const svgRef = React.useRef<SVGSVGElement | null>(null);
  const dragRef = React.useRef<
    | { kind: 'draw'; start: [number, number] }
    | { kind: 'move'; start: [number, number]; origin: Rect }
    | { kind: 'resize'; handle: string; origin: Rect; start: [number, number] }
    | null
  >(null);

  const frame = frames[frameIndex] ?? frames[0];
  const fallbackFrameUrl = frame?.url
    ? null
    : (tsCfg.baseUrl ? getSourcePreviewFrameUrl(tsCfg, videoId, projectId, videoId) : null);
  const imageUrl = frame?.url ?? fallbackFrameUrl;
  const { width, height } = effectiveSize;

  React.useEffect(() => {
    setEffectiveSize(videoSize);
  }, [videoSize.width, videoSize.height, videoId]);

  React.useEffect(() => {
    if (!tsCfg.baseUrl) return;
    getTimestampRegion(tsCfg, videoId, projectId)
      .then((r) => {
        if (r.method?.startsWith('manual')) {
          setSaved(r);
          setDraft({ x: r.x, y: r.y, w: r.w, h: r.h });
        }
      })
      .catch(() => { /* no saved region */ });
  }, [tsCfg, videoId, projectId]);

  const activeRect = draft ?? (saved ? { x: saved.x, y: saved.y, w: saved.w, h: saved.h } : null);

  function onPointerDown(e: React.PointerEvent<SVGSVGElement>) {
    if (disabled || !svgRef.current) return;
    const [px, py] = screenToSvg(svgRef.current, e.clientX, e.clientY);
    if (activeRect) {
      const handles: Record<string, [number, number]> = {
        nw: [activeRect.x, activeRect.y],
        ne: [activeRect.x + activeRect.w, activeRect.y],
        sw: [activeRect.x, activeRect.y + activeRect.h],
        se: [activeRect.x + activeRect.w, activeRect.y + activeRect.h],
      };
      for (const [name, [hx, hy]] of Object.entries(handles)) {
        if (Math.hypot(px - hx, py - hy) <= HANDLE_R + 4) {
          dragRef.current = { kind: 'resize', handle: name, origin: activeRect, start: [px, py] };
          e.currentTarget.setPointerCapture(e.pointerId);
          return;
        }
      }
      if (
        px >= activeRect.x && px <= activeRect.x + activeRect.w
        && py >= activeRect.y && py <= activeRect.y + activeRect.h
      ) {
        dragRef.current = { kind: 'move', start: [px, py], origin: activeRect };
        e.currentTarget.setPointerCapture(e.pointerId);
        return;
      }
    }
    dragRef.current = { kind: 'draw', start: [px, py] };
    setDraft({ x: px, y: py, w: 1, h: 1 });
    e.currentTarget.setPointerCapture(e.pointerId);
  }

  function onPointerMove(e: React.PointerEvent<SVGSVGElement>) {
    if (!dragRef.current || !svgRef.current) return;
    const [px, py] = screenToSvg(svgRef.current, e.clientX, e.clientY);
    const d = dragRef.current;
    if (d.kind === 'draw') {
      const x0 = Math.min(d.start[0], px);
      const y0 = Math.min(d.start[1], py);
      const w = Math.abs(px - d.start[0]);
      const h = Math.abs(py - d.start[1]);
      setDraft(clampRect({ x: x0, y: y0, w, h }, width, height));
    } else if (d.kind === 'move') {
      const dx = px - d.start[0];
      const dy = py - d.start[1];
      setDraft(clampRect({
        x: d.origin.x + dx,
        y: d.origin.y + dy,
        w: d.origin.w,
        h: d.origin.h,
      }, width, height));
    } else if (d.kind === 'resize') {
      const o = d.origin;
      let x = o.x;
      let y = o.y;
      let x2 = o.x + o.w;
      let y2 = o.y + o.h;
      if (d.handle.includes('w')) x = px;
      if (d.handle.includes('e')) x2 = px;
      if (d.handle.includes('n')) y = py;
      if (d.handle.includes('s')) y2 = py;
      setDraft(clampRect({
        x: Math.min(x, x2),
        y: Math.min(y, y2),
        w: Math.abs(x2 - x),
        h: Math.abs(y2 - y),
      }, width, height));
    }
  }

  function onPointerUp() {
    dragRef.current = null;
  }

  async function handleSave() {
    if (!draft || !tsCfg.baseUrl) return;
    setBusy(true);
    setError(null);
    try {
      const r = await putTimestampRegion(tsCfg, videoId, projectId, {
        x: Math.round(draft.x),
        y: Math.round(draft.y),
        w: Math.round(draft.w),
        h: Math.round(draft.h),
        source_frame_index: frame?.url ? (frame?.index ?? frameIndex) : undefined,
        video_width: Math.round(width),
        video_height: Math.round(height),
      });
      setSaved(r);
      setPreviewKey((k) => k + 1);
      onRegionSaved?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function handleClearAuto() {
    if (!tsCfg.baseUrl) return;
    setBusy(true);
    setError(null);
    try {
      await deleteTimestampRegion(tsCfg, videoId, projectId);
      setSaved(null);
      setDraft(null);
      setPreviewKey((k) => k + 1);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  if (!imageUrl) {
    return (
      <p className="muted timestamp-region-editor-hint">
        No preview frame available — ensure the video source file is accessible.
      </p>
    );
  }

  return (
    <div className="timestamp-region-editor">
      <p className="muted timestamp-region-editor-hint">
        Drag a box over the burned-in timestamp before running the scan. Auto-detect is skipped when a region is saved.
      </p>
      <div className="timestamp-region-editor-canvas-wrap">
        <img
          className="timestamp-region-editor-img"
          src={imageUrl}
          alt={frame?.url ? `Scene ${frameIndex + 1} keyframe` : 'Source video preview'}
          draggable={false}
          onLoad={(e) => {
            const img = e.currentTarget;
            if (img.naturalWidth > 0 && img.naturalHeight > 0) {
              setEffectiveSize({ width: img.naturalWidth, height: img.naturalHeight });
            }
          }}
        />
        <svg
          ref={svgRef}
          className="timestamp-region-editor-svg"
          viewBox={`0 0 ${width} ${height}`}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerLeave={onPointerUp}
        >
          {activeRect && (
            <>
              <rect
                className="timestamp-region-rect"
                x={activeRect.x}
                y={activeRect.y}
                width={activeRect.w}
                height={activeRect.h}
              />
              {(['nw', 'ne', 'sw', 'se'] as const).map((h) => {
                const cx = h.includes('e') ? activeRect.x + activeRect.w : activeRect.x;
                const cy = h.includes('s') ? activeRect.y + activeRect.h : activeRect.y;
                return (
                  <circle
                    key={h}
                    className="timestamp-region-handle"
                    cx={cx}
                    cy={cy}
                    r={HANDLE_R}
                  />
                );
              })}
            </>
          )}
        </svg>
      </div>
      {activeRect && (
        <p className="muted timestamp-region-dims">
          {Math.round(activeRect.w)}×{Math.round(activeRect.h)} px at ({Math.round(activeRect.x)}, {Math.round(activeRect.y)})
        </p>
      )}
      <div className="timestamp-region-editor-actions">
        <button
          type="button"
          className="toolbar-button small primary"
          disabled={disabled || busy || !draft}
          onClick={handleSave}
        >
          {busy ? 'Saving…' : 'Save region'}
        </button>
        <button
          type="button"
          className="toolbar-button small"
          disabled={disabled || busy || (!saved && !draft)}
          onClick={() => { setDraft(null); }}
        >
          Clear draft
        </button>
        {saved?.method?.startsWith('manual') && (
          <button
            type="button"
            className="toolbar-button small"
            disabled={disabled || busy}
            onClick={handleClearAuto}
          >
            Use auto-detect
          </button>
        )}
      </div>
      {saved?.method?.startsWith('manual') && (
        <div className="timestamp-region-saved-preview">
          <img
            src={getTimestampRegionPreviewUrl(tsCfg, videoId, projectId, previewKey)}
            alt="Saved timestamp crop"
            className="timestamp-region-preview"
          />
          <span className="muted">Saved manual region · OCR uses this crop only</span>
        </div>
      )}
      {error && <p className="timestamp-error">{error.slice(0, 200)}</p>}
    </div>
  );
}