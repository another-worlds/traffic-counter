export type LayerKey = 'saved-lines' | 'frame-scrubber' | 'direction-overlay' | 'counts' | 'heatmap' | 'trajectories' | 'suggestions';

export type Point = [number, number];

export type LineGeometry = {
  id: string;
  name: string;
  color: string;
  kind: 'line' | 'polyline';
  points: Point[];
  locked?: boolean;
  count?: number;
};

export type ViewportSpec = {
  projectId: string;
  videoIds: string[];
  selectedLineIds: string[];
  frameCount: number;
  activeLayers: LayerKey[];
};

export type VideoSize = { width: number; height: number };

export type LineCountByDirection = { positive?: number; negative?: number };
export type LineCountByClass = Record<string, number>;

export type LineCount = {
  line_id: string;
  line_name: string;
  total: number;
  by_direction: LineCountByDirection;
  by_class: LineCountByClass;
  percent_of_video_total?: number;
  percent_of_drawn_lines?: number;
};

export type CountsBundle = {
  total_unique_tracks: number;
  per_line: Record<string, LineCount>;
};

export type TrackStats = {
  total_tracks: number;
  direction_bins?: { right?: number; left?: number; up?: number; down?: number };
  by_class?: Record<string, number>;
  avg_track_frames?: number;
  busy_zone?: { cx_pct: number; cy_pct: number; r_pct: number } | null;
};

export type Suggestion = {
  name: string;
  color: string;
  score: number;
  points: { a: [number, number]; b: [number, number] };
};

export type InteractionState =
  | { kind: 'idle' }
  | { kind: 'drawing'; draftLineId: string }
  | { kind: 'moving'; lineId: string; anchor: Point; original: Point[] }
  | { kind: 'resizing'; lineId: string; handleIndex: number };

export type PendingAction =
  | { type: 'request-suggestions'; n: number }
  | { type: 'accept-suggestion'; suggestion: Suggestion }
  | { type: 'dismiss-suggestions' };

export type OverlayModel = {
  spec: ViewportSpec;
  currentFrame: number;
  selectedLineId: string | null;
  lines: LineGeometry[];
  visibleLayers: Record<LayerKey, boolean>;
  interaction: InteractionState;
  pendingActions: PendingAction[];
};

export type BridgePayload = {
  kind: 'overlay-snapshot';
  projectId: string;
  videoIds: string[];
  selectedLineId: string | null;
  currentFrame: number;
  activeLayers: LayerKey[];
  lines: LineGeometry[];
  pendingActions: PendingAction[];
};

export type HostViewportBootstrap = {
  spec?: Partial<ViewportSpec>;
  initialLines?: ApiLine[] | LineGeometry[];
  frameUrl?: string;
  trajectoriesUrl?: string;
  heatmapUrl?: string;
  videoSize?: VideoSize;
  trackStats?: TrackStats;
  counts?: CountsBundle;
  suggestions?: Suggestion[];
};

/** Server-side line shape (from API: points = {a:[x,y], b:[x,y]}). */
export type ApiLine = {
  id: string;
  name: string;
  color: string;
  points: { a: [number, number]; b: [number, number] };
};

export type OverlayAction =
  | { type: 'set-frame'; frame: number }
  | { type: 'toggle-layer'; layer: LayerKey }
  | { type: 'select-line'; lineId: string | null }
  | { type: 'add-line'; line: LineGeometry }
  | { type: 'update-line'; lineId: string; patch: Partial<LineGeometry> }
  | { type: 'delete-line'; lineId: string }
  | { type: 'replace-lines'; lines: LineGeometry[] }
  | { type: 'start-draw'; point: Point; color: string }
  | { type: 'update-draft'; point: Point }
  | { type: 'commit-draft' }
  | { type: 'cancel-draft' }
  | { type: 'start-move'; lineId: string; anchor: Point }
  | { type: 'update-move'; point: Point }
  | { type: 'commit-move' }
  | { type: 'start-resize-handle'; lineId: string; handleIndex: number }
  | { type: 'update-resize-handle'; point: Point }
  | { type: 'commit-resize-handle' }
  | { type: 'queue-action'; action: PendingAction }
  | { type: 'clear-pending-actions' };

function cloneLine(line: LineGeometry): LineGeometry {
  return {
    ...line,
    points: line.points.map(([x, y]) => [x, y] as Point),
  };
}

/** Convert API line shape ({a, b}) into LineGeometry. Passes through if already in the right shape. */
function adaptInitialLine(raw: ApiLine | LineGeometry): LineGeometry {
  if ('kind' in raw && Array.isArray((raw as LineGeometry).points)) {
    return cloneLine(raw as LineGeometry);
  }
  const api = raw as ApiLine;
  return {
    id: String(api.id),
    name: api.name ?? 'line',
    color: api.color ?? '#e24b4a',
    kind: 'line',
    points: [
      [Number(api.points?.a?.[0] ?? 0), Number(api.points?.a?.[1] ?? 0)],
      [Number(api.points?.b?.[0] ?? 0), Number(api.points?.b?.[1] ?? 0)],
    ],
  };
}

export function createDefaultOverlayModel(spec: ViewportSpec, lines: LineGeometry[] = []): OverlayModel {
  const visibleLayers: Record<LayerKey, boolean> = {
    'saved-lines': true,
    'frame-scrubber': true,
    'direction-overlay': true,
    counts: true,
    heatmap: false,
    trajectories: true,
    suggestions: false,
  };

  for (const layer of Object.keys(visibleLayers) as LayerKey[]) {
    if (spec.activeLayers.includes(layer)) {
      visibleLayers[layer] = true;
    }
  }

  return {
    spec,
    currentFrame: 0,
    selectedLineId: lines[0]?.id ?? null,
    lines,
    visibleLayers,
    interaction: { kind: 'idle' },
    pendingActions: [],
  };
}

function genId(): string {
  if (typeof crypto !== 'undefined' && (crypto as Crypto).randomUUID) {
    return (crypto as Crypto).randomUUID();
  }
  return `line-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
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
      return { ...model, selectedLineId: action.lineId };

    case 'add-line': {
      const nextLines = [...model.lines, cloneLine(action.line)];
      return { ...model, lines: nextLines, selectedLineId: action.line.id };
    }

    case 'update-line': {
      const nextLines = model.lines.map((line) => {
        if (line.id !== action.lineId) return line;
        return {
          ...line,
          ...action.patch,
          points: action.patch.points
            ? action.patch.points.map(([x, y]) => [x, y] as Point)
            : line.points,
        };
      });
      return { ...model, lines: nextLines };
    }

    case 'delete-line': {
      const nextLines = model.lines.filter((line) => line.id !== action.lineId);
      return {
        ...model,
        lines: nextLines,
        selectedLineId:
          model.selectedLineId === action.lineId ? (nextLines[0]?.id ?? null) : model.selectedLineId,
      };
    }

    case 'replace-lines': {
      const next = action.lines.map(cloneLine);
      const stillSelected = next.find((l) => l.id === model.selectedLineId);
      return {
        ...model,
        lines: next,
        selectedLineId: stillSelected ? model.selectedLineId : (next[0]?.id ?? null),
      };
    }

    case 'start-draw': {
      const id = genId();
      const draft: LineGeometry = {
        id,
        name: `line ${model.lines.length + 1}`,
        color: action.color,
        kind: 'line',
        points: [action.point, action.point],
      };
      return {
        ...model,
        lines: [...model.lines, draft],
        selectedLineId: id,
        interaction: { kind: 'drawing', draftLineId: id },
      };
    }

    case 'update-draft': {
      if (model.interaction.kind !== 'drawing') return model;
      const draftId = model.interaction.draftLineId;
      const nextLines = model.lines.map((line) => {
        if (line.id !== draftId) return line;
        return { ...line, points: [line.points[0], action.point] };
      });
      return { ...model, lines: nextLines };
    }

    case 'commit-draft': {
      if (model.interaction.kind !== 'drawing') return model;
      const draftId = model.interaction.draftLineId;
      const draft = model.lines.find((l) => l.id === draftId);
      if (!draft) return { ...model, interaction: { kind: 'idle' } };
      const [ax, ay] = draft.points[0];
      const [bx, by] = draft.points[1];
      const length = Math.hypot(bx - ax, by - ay);
      if (length < 30) {
        // Too short — discard.
        return {
          ...model,
          lines: model.lines.filter((l) => l.id !== draftId),
          selectedLineId: null,
          interaction: { kind: 'idle' },
        };
      }
      return { ...model, interaction: { kind: 'idle' } };
    }

    case 'cancel-draft': {
      if (model.interaction.kind !== 'drawing') return model;
      const draftId = model.interaction.draftLineId;
      return {
        ...model,
        lines: model.lines.filter((l) => l.id !== draftId),
        selectedLineId: null,
        interaction: { kind: 'idle' },
      };
    }

    case 'start-move': {
      const line = model.lines.find((l) => l.id === action.lineId);
      if (!line) return model;
      return {
        ...model,
        selectedLineId: action.lineId,
        interaction: {
          kind: 'moving',
          lineId: action.lineId,
          anchor: action.anchor,
          original: line.points.map(([x, y]) => [x, y] as Point),
        },
      };
    }

    case 'update-move': {
      if (model.interaction.kind !== 'moving') return model;
      const { lineId, anchor, original } = model.interaction;
      const dx = action.point[0] - anchor[0];
      const dy = action.point[1] - anchor[1];
      const nextLines = model.lines.map((line) => {
        if (line.id !== lineId) return line;
        return {
          ...line,
          points: original.map(([x, y]) => [x + dx, y + dy] as Point),
        };
      });
      return { ...model, lines: nextLines };
    }

    case 'commit-move':
      return { ...model, interaction: { kind: 'idle' } };

    case 'start-resize-handle': {
      const line = model.lines.find((l) => l.id === action.lineId);
      if (!line) return model;
      return {
        ...model,
        selectedLineId: action.lineId,
        interaction: { kind: 'resizing', lineId: action.lineId, handleIndex: action.handleIndex },
      };
    }

    case 'update-resize-handle': {
      if (model.interaction.kind !== 'resizing') return model;
      const { lineId, handleIndex } = model.interaction;
      const nextLines = model.lines.map((line) => {
        if (line.id !== lineId) return line;
        const points = line.points.map(([x, y], i) =>
          i === handleIndex ? action.point : ([x, y] as Point),
        );
        return { ...line, points };
      });
      return { ...model, lines: nextLines };
    }

    case 'commit-resize-handle':
      return { ...model, interaction: { kind: 'idle' } };

    case 'queue-action':
      return { ...model, pendingActions: [...model.pendingActions, action.action] };

    case 'clear-pending-actions':
      return { ...model, pendingActions: [] };

    default:
      return model;
  }
}

export function buildBridgePayload(model: OverlayModel): BridgePayload {
  const activeLayers = (Object.keys(model.visibleLayers) as LayerKey[]).filter(
    (layer) => model.visibleLayers[layer],
  );

  // Don't include the draft line in snapshots — it's local until committed.
  const interaction = model.interaction;
  const exportLines =
    interaction.kind === 'drawing'
      ? model.lines.filter((l) => l.id !== interaction.draftLineId)
      : model.lines;

  return {
    kind: 'overlay-snapshot',
    projectId: model.spec.projectId,
    videoIds: [...model.spec.videoIds],
    selectedLineId: model.selectedLineId,
    currentFrame: model.currentFrame,
    activeLayers,
    lines: exportLines.map(cloneLine),
    pendingActions: [...model.pendingActions],
  };
}

export function buildViewportSpecFromBootstrap(bootstrap?: HostViewportBootstrap): ViewportSpec {
  return {
    projectId: bootstrap?.spec?.projectId ?? 'preview',
    videoIds: bootstrap?.spec?.videoIds ?? [],
    selectedLineIds: bootstrap?.spec?.selectedLineIds ?? [],
    frameCount: bootstrap?.spec?.frameCount ?? 100,
    activeLayers:
      bootstrap?.spec?.activeLayers ?? ['saved-lines', 'frame-scrubber', 'direction-overlay', 'counts', 'trajectories'],
  };
}

export function buildInitialLinesFromBootstrap(bootstrap?: HostViewportBootstrap): LineGeometry[] {
  const rawLines = (bootstrap?.initialLines ?? []) as (ApiLine | LineGeometry)[];
  return rawLines.map(adaptInitialLine);
}
