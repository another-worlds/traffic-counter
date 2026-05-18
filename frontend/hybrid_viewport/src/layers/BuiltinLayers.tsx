import type { Layer, LayerRenderContext } from './types';

const FrameLayer: Layer = {
  key: 'frame-scrubber',
  label: 'Frame',
  defaultVisible: true,
  render({ model, bootstrap, videoSize }: LayerRenderContext) {
    const { width, height } = videoSize;
    // Scene-based frames take priority; fall back to legacy single frameUrl.
    const frameUrl =
      bootstrap.frames?.[model.currentFrame]?.url ?? bootstrap.frameUrl ?? null;
    return frameUrl ? (
      <image
        key="frame"
        href={frameUrl}
        x={0} y={0}
        width={width} height={height}
        preserveAspectRatio="none"
        style={{ pointerEvents: 'none' }}
      />
    ) : (
      <g key="frame-bg" style={{ pointerEvents: 'none' }}>
        <rect width={width} height={height} fill="#0e1117" />
        <text
          x={width / 2}
          y={height / 2}
          textAnchor="middle"
          dominantBaseline="middle"
          fill="#8b959e"
          fontSize={Math.round(Math.min(width, height) * 0.035)}
          fontFamily="system-ui, sans-serif"
        >
          No preview frame available — re-analyze the video to regenerate it.
        </text>
      </g>
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
