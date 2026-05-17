import React from 'react';
import type {
  CountsBundle,
  HostViewportBootstrap,
  LayerKey,
  LineGeometry,
  OverlayAction,
  OverlayModel,
  Suggestion,
  TrackStats,
} from './viewportState';
import { BUILTIN_PANELS } from './panels';
import type { PanelSectionProps } from './panels';

type SidePanelProps = {
  model: OverlayModel;
  bootstrap: HostViewportBootstrap;
  trackStats?: TrackStats;
  counts?: CountsBundle;
  suggestions?: Suggestion[];
  drawingColor: string;
  activeTool: string;
  onDrawingColorChange: (color: string) => void;
  onUpdateLine: (lineId: string, patch: Partial<LineGeometry>) => void;
  onDeleteLine: (lineId: string) => void;
  onSelectLine: (lineId: string | null) => void;
  onToggleLayer: (layer: LayerKey) => void;
  onRequestSuggestions: (n: number) => void;
  onAcceptSuggestion: (s: Suggestion) => void;
  onDismissSuggestions: () => void;
  dispatch: (action: OverlayAction) => void;
};

export default function SidePanel(props: SidePanelProps) {
  const sectionProps: PanelSectionProps = props;

  const panels = [...BUILTIN_PANELS]
    .sort((a, b) => a.order - b.order)
    .filter((panel) => !panel.shouldShow || panel.shouldShow(sectionProps));

  return (
    <aside className="side-panel">
      {panels.map(({ id, Component }) => (
        <Component key={id} {...sectionProps} />
      ))}
    </aside>
  );
}
