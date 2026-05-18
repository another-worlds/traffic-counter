import type React from 'react';
import type { LineGeometry, OverlayAction, OverlayModel, Point, VideoSize } from '../viewportState';

export type ToolContext = {
  model: OverlayModel;
  dispatch: (action: OverlayAction) => void;
  videoSize: VideoSize;
};

export interface Tool {
  id: string;
  label: string;
  icon?: string;
  /** CSS cursor when this tool is active over empty viewport area. */
  cursor: string;
  onMouseDownEmpty?(point: Point, ctx: ToolContext): void;
  onMouseDownLine?(lineId: string, point: Point, ctx: ToolContext): void;
  onMouseDownHandle?(lineId: string, handleIndex: number, ctx: ToolContext): void;
  onMouseMove?(point: Point, ctx: ToolContext): void;
  onMouseUp?(point: Point, ctx: ToolContext): void;
  onKeyDown?(e: KeyboardEvent, ctx: ToolContext): void;
  /** SVG nodes rendered above lines (for rubber-band previews, etc.). */
  renderOverlay?(ctx: ToolContext): React.ReactNode;
}
