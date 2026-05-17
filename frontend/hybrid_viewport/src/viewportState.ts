export type LayerKey = 'saved-lines' | 'frame-scrubber' | 'direction-overlay' | 'counts' | 'heatmap' | 'suggestions';

export type Point = [number, number];

export type LineGeometry = {
  id: string;
  name: string;
  color: string;
  kind: 'line' | 'polyline';
  points: Point[];
  locked?: boolean;
};

export type ViewportSpec = {
  projectId: string;
  videoIds: string[];
  selectedLineIds: string[];
  frameCount: number;
  activeLayers: LayerKey[];
};

export type OverlayModel = {
  spec: ViewportSpec;
  currentFrame: number;
  selectedLineId: string | null;
  lines: LineGeometry[];
  visibleLayers: Record<LayerKey, boolean>;
};

export type BridgePayload = {
  kind: 'overlay-snapshot';
  projectId: string;
  videoIds: string[];
  selectedLineId: string | null;
  currentFrame: number;
  activeLayers: LayerKey[];
  lines: LineGeometry[];
};

export type HostViewportBootstrap = {
  spec?: Partial<ViewportSpec>;
  initialLines?: LineGeometry[];
};

export type OverlayAction =
  | { type: 'set-frame'; frame: number }
  | { type: 'toggle-layer'; layer: LayerKey }
  | { type: 'select-line'; lineId: string | null }
  | { type: 'add-line'; line: LineGeometry }
  | { type: 'update-line'; lineId: string; patch: Partial<LineGeometry> }
  | { type: 'delete-line'; lineId: string }
  | { type: 'replace-lines'; lines: LineGeometry[] };

function cloneLine(line: LineGeometry): LineGeometry {
  return {
    ...line,
    points: line.points.map(([x, y]) => [x, y] as Point),
  };
}

export function createDefaultOverlayModel(spec: ViewportSpec, lines: LineGeometry[] = []): OverlayModel {
  const visibleLayers: Record<LayerKey, boolean> = {
    'saved-lines': true,
    'frame-scrubber': true,
    'direction-overlay': true,
    counts: true,
    heatmap: false,
    suggestions: false,
  };

  for (const layer of Object.keys(visibleLayers) as LayerKey[]) {
    visibleLayers[layer] = spec.activeLayers.includes(layer);
  }

  return {
    spec,
    currentFrame: 0,
    selectedLineId: lines[0]?.id ?? null,
    lines,
    visibleLayers,
  };
}

export function createDemoLine(index: number): LineGeometry {
  const offset = 80 + index * 22;
  // Use a timestamp suffix to prevent ID collisions when lines are added after deletions.
  const id = typeof crypto !== 'undefined' && crypto.randomUUID ? crypto.randomUUID() : `demo-${Date.now()}-${index}`;
  return {
    id,
    name: `line ${index + 1}`,
    color: index % 2 === 0 ? '#e24b4a' : '#4ecdc4',
    kind: 'line',
    points: [
      [offset, 120 + index * 10],
      [offset + 160, 260 + index * 12],
    ],
  };
}

export function reduceOverlayModel(model: OverlayModel, action: OverlayAction): OverlayModel {
  switch (action.type) {
    case 'set-frame':
      return {
        ...model,
        currentFrame: Math.max(0, Math.min(action.frame, Math.max(model.spec.frameCount - 1, 0))),
      };
    case 'toggle-layer':
      return {
        ...model,
        visibleLayers: {
          ...model.visibleLayers,
          [action.layer]: !model.visibleLayers[action.layer],
        },
      };
    case 'select-line':
      return {
        ...model,
        selectedLineId: action.lineId,
      };
    case 'add-line': {
      const nextLines = [...model.lines, cloneLine(action.line)];
      return {
        ...model,
        lines: nextLines,
        selectedLineId: action.line.id,
      };
    }
    case 'update-line': {
      const nextLines = model.lines.map((line) => {
        if (line.id !== action.lineId) {
          return line;
        }
        return {
          ...line,
          ...action.patch,
          points: action.patch.points ? action.patch.points.map(([x, y]) => [x, y] as Point) : line.points,
        };
      });
      return {
        ...model,
        lines: nextLines,
      };
    }
    case 'delete-line': {
      const nextLines = model.lines.filter((line) => line.id !== action.lineId);
      return {
        ...model,
        lines: nextLines,
        selectedLineId: model.selectedLineId === action.lineId ? nextLines[0]?.id ?? null : model.selectedLineId,
      };
    }
    case 'replace-lines':
      return {
        ...model,
        lines: action.lines.map(cloneLine),
        selectedLineId: action.lines[0]?.id ?? null,
      };
    default:
      return model;
  }
}

export function buildBridgePayload(model: OverlayModel): BridgePayload {
  const activeLayers = (Object.keys(model.visibleLayers) as LayerKey[]).filter((layer) => model.visibleLayers[layer]);

  return {
    kind: 'overlay-snapshot',
    projectId: model.spec.projectId,
    videoIds: [...model.spec.videoIds],
    selectedLineId: model.selectedLineId,
    currentFrame: model.currentFrame,
    activeLayers,
    lines: model.lines.map(cloneLine),
  };
}

export function buildViewportSpecFromBootstrap(bootstrap?: HostViewportBootstrap): ViewportSpec {
  const base: ViewportSpec = {
    projectId: bootstrap?.spec?.projectId ?? 'preview',
    videoIds: bootstrap?.spec?.videoIds ?? [],
    selectedLineIds: bootstrap?.spec?.selectedLineIds ?? [],
    frameCount: bootstrap?.spec?.frameCount ?? 100,
    activeLayers: bootstrap?.spec?.activeLayers ?? ['saved-lines', 'frame-scrubber', 'direction-overlay', 'counts'],
  };

  return base;
}

export function buildInitialLinesFromBootstrap(bootstrap?: HostViewportBootstrap): LineGeometry[] {
  return (bootstrap?.initialLines ?? []).map(cloneLine);
}
