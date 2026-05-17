import type React from 'react';
import type {
  CountsBundle,
  HostViewportBootstrap,
  LayerKey,
  LineGeometry,
  OverlayAction,
  OverlayModel,
  Suggestion,
  TrackStats,
} from '../viewportState';

export type PanelSectionProps = {
  model: OverlayModel;
  bootstrap: HostViewportBootstrap;
  trackStats?: TrackStats;
  counts?: CountsBundle;
  suggestions?: Suggestion[];
  drawingColor: string;
  activeTool: string;
  onDrawingColorChange(color: string): void;
  onUpdateLine(lineId: string, patch: Partial<LineGeometry>): void;
  onDeleteLine(lineId: string): void;
  onSelectLine(lineId: string | null): void;
  onToggleLayer(layer: LayerKey): void;
  onRequestSuggestions(n: number): void;
  onAcceptSuggestion(s: Suggestion): void;
  onDismissSuggestions(): void;
  dispatch(action: OverlayAction): void;
};

export interface PanelSection {
  id: string;
  order: number;
  shouldShow?(props: PanelSectionProps): boolean;
  Component: React.FC<PanelSectionProps>;
}
