import React from 'react';
import type { Layer, LayerRenderContext } from './types';

function FrameFallback({ width, height }: { width: number; height: number }) {
  const titleSize = Math.round(Math.min(width, height) * 0.04);
  const subtitleSize = Math.round(Math.min(width, height) * 0.028);
  return (
    <g key="frame-bg" style={{ pointerEvents: 'none' }}>
      <rect width={width} height={height} fill="#0e1117" />
      <text
        x={width / 2}
        y={height / 2 - titleSize * 0.6}
        textAnchor="middle"
        dominantBaseline="middle"
        fill="#fafafa"
        fontSize={titleSize}
        fontFamily="system-ui, sans-serif"
      >
        No preview frame
      </text>
      <text
        x={width / 2}
        y={height / 2 + titleSize}
        textAnchor="middle"
        dominantBaseline="middle"
        fill="#8b959e"
        fontSize={subtitleSize}
        fontFamily="system-ui, sans-serif"
      >
        Re-analyze the video to regenerate it
      </text>
    </g>
  );
}

function FrameImage({ frameUrl, width, height }: { frameUrl: string | null; width: number; height: number }) {
  const [loadFailed, setLoadFailed] = React.useState(false);
  // Reset the failure flag whenever the URL changes so scrubbing to
  // another scene re-attempts the load instead of staying on the fallback.
  React.useEffect(() => {
    setLoadFailed(false);
  }, [frameUrl]);

  if (!frameUrl || loadFailed) {
    return <FrameFallback width={width} height={height} />;
  }
  return (
    <image
      key={`frame:${frameUrl}`}
      href={frameUrl}
      x={0} y={0}
      width={width} height={height}
      preserveAspectRatio="none"
      style={{ pointerEvents: 'none' }}
      onError={() => setLoadFailed(true)}
    />
  );
}

const FrameLayer: Layer = {
  key: 'frame-scrubber',
  label: 'Frame',
  defaultVisible: true,
  render({ model, bootstrap, videoSize }: LayerRenderContext) {
    // Scene-based frames take priority; fall back to legacy single frameUrl.
    const frameUrl =
      bootstrap.frames?.[model.currentFrame]?.url ?? bootstrap.frameUrl ?? null;
    return (
      <FrameImage
        key="frame"
        frameUrl={frameUrl}
        width={videoSize.width}
        height={videoSize.height}
      />
    );
  },
};

const TrajectoriesLayer: Layer = {
  key: 'trajectories',
  label: 'Trajectories',
  defaultVisible: true,
  render({ model, bootstrap, videoSize }: LayerRenderContext) {
    if (!model.visibleLayers['trajectories'] || !bootstrap.trajectoriesUrl) return null;
    const { width, height } = videoSize;
    return (
      <image
        key="traj"
        href={bootstrap.trajectoriesUrl}
        x={0} y={0}
        width={width} height={height}
        preserveAspectRatio="none"
        style={{ pointerEvents: 'none', opacity: 0.85 }}
      />
    );
  },
};

const HeatmapLayer: Layer = {
  key: 'heatmap',
  label: 'Heatmap',
  defaultVisible: false,
  render({ model, bootstrap, videoSize }: LayerRenderContext) {
    if (!model.visibleLayers['heatmap'] || !bootstrap.heatmapUrl) return null;
    const { width, height } = videoSize;
    return (
      <image
        key="heatmap"
        href={bootstrap.heatmapUrl}
        x={0} y={0}
        width={width} height={height}
        preserveAspectRatio="none"
        style={{ pointerEvents: 'none', mixBlendMode: 'screen' as React.CSSProperties['mixBlendMode'] }}
      />
    );
  },
};

export const BUILTIN_IMAGE_LAYERS: Layer[] = [FrameLayer, TrajectoriesLayer, HeatmapLayer];
