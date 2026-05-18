import type React from 'react';
import type { HostViewportBootstrap, LayerKey, OverlayModel, VideoSize } from '../viewportState';

export interface LayerRenderContext {
  model: OverlayModel;
  bootstrap: HostViewportBootstrap;
  videoSize: VideoSize;
}

export interface Layer {
  key: LayerKey;
  label: string;
  defaultVisible: boolean;
  /** Returns an SVG fragment (or null) for this layer's visual. */
  render(ctx: LayerRenderContext): React.ReactNode;
}
