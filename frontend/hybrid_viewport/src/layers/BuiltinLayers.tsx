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
      <rect key="frame-bg" width={width} height={height} fill="#0e1117" />
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
