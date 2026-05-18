import type { Tool, ToolContext } from './types';
import type { Point } from '../viewportState';

const MOVE_THRESHOLD = 3;

type PendingSelect = { lineId: string; anchor: Point } | null;

// Module-level refs because Tool instances are singletons (not per-React-render).
let _pendingSelect: PendingSelect = null;
let _pendingDrawStart: Point | null = null;
let _drawStarted = false;

export const LineTool: Tool = {
  id: 'line',
  label: 'Line',
  icon: '✏️',
  cursor: 'crosshair',

  onMouseDownEmpty(point, { model, dispatch }) {
    if (model.interaction.kind !== 'idle') return;
    _pendingSelect = null;
    _pendingDrawStart = point;
    _drawStarted = false;
    dispatch({ type: 'select-line', lineId: null });
    // start-draw is deferred to first meaningful drag in onMouseMove
  },

  onMouseDownLine(lineId, point, { model, dispatch }) {
    if (model.interaction.kind !== 'idle') return;
    _pendingSelect = { lineId, anchor: point };
    dispatch({ type: 'select-line', lineId });
  },

  onMouseDownHandle(lineId, handleIndex, { model, dispatch }) {
    if (model.interaction.kind !== 'idle') return;
    _pendingSelect = null;
    dispatch({ type: 'start-resize-handle', lineId, handleIndex });
  },

  onMouseMove(point, { model, dispatch }) {
    const pending = _pendingSelect;
    if (pending) {
      const dx = point[0] - pending.anchor[0];
      const dy = point[1] - pending.anchor[1];
      if (Math.hypot(dx, dy) > MOVE_THRESHOLD) {
        _pendingSelect = null;
        dispatch({ type: 'start-move', lineId: pending.lineId, anchor: pending.anchor });
        dispatch({ type: 'update-move', point });
      }
      return;
    }
    if (_pendingDrawStart !== null) {
      if (!_drawStarted) {
        const dx = point[0] - _pendingDrawStart[0];
        const dy = point[1] - _pendingDrawStart[1];
        if (Math.hypot(dx, dy) > MOVE_THRESHOLD) {
          _drawStarted = true;
          dispatch({ type: 'start-draw', point: _pendingDrawStart, color: model.drawingColor ?? '#e24b4a' });
          dispatch({ type: 'update-draft', point });
        }
      } else {
        // dispatch uses functional updaters so this correctly applies after start-draw
        dispatch({ type: 'update-draft', point });
      }
      return;
    }
    if (model.interaction.kind === 'moving') {
      dispatch({ type: 'update-move', point });
    } else if (model.interaction.kind === 'resizing') {
      dispatch({ type: 'update-resize-handle', point });
    }
  },

  onMouseUp(_point, { model, dispatch }) {
    _pendingSelect = null;
    _pendingDrawStart = null;
    _drawStarted = false;
    if (model.interaction.kind === 'drawing') {
      dispatch({ type: 'commit-draft' });
    } else if (model.interaction.kind === 'moving') {
      dispatch({ type: 'commit-move' });
    } else if (model.interaction.kind === 'resizing') {
      dispatch({ type: 'commit-resize-handle' });
    }
  },

  onKeyDown(e, { model, dispatch }) {
    const tag = (e.target as HTMLElement)?.tagName ?? '';
    if (tag === 'INPUT' || tag === 'TEXTAREA') return;
    if (model.selectedLineId && (e.key === 'Delete' || e.key === 'Backspace')) {
      e.preventDefault();
      dispatch({ type: 'delete-line', lineId: model.selectedLineId });
    } else if (e.key === 'Escape') {
      if (model.interaction.kind === 'drawing') {
        dispatch({ type: 'cancel-draft' });
      } else {
        dispatch({ type: 'select-line', lineId: null });
      }
    }
  },
};

export function getCursor(model: import('../viewportState').OverlayModel): string {
  if (model.interaction.kind === 'moving') return 'grabbing';
  if (model.interaction.kind === 'resizing') return 'nwse-resize';
  return 'crosshair';
}
