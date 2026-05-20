import React from 'react';
import type { CountsBundle, HostViewportBootstrap, LineGeometry, OverlayModel, Point, VideoSize } from './viewportState';
import { BUILTIN_IMAGE_LAYERS } from './layers';
import type { LayerRenderContext } from './layers';
import { getCursor } from './tools';

type ViewportProps = {
  model: OverlayModel;
  bootstrap: HostViewportBootstrap;
  videoSize: VideoSize;
  counts?: CountsBundle;
  onMouseDownEmpty: (point: Point) => void;
  onMouseDownLine: (lineId: string, point: Point) => void;
  onMouseDownHandle: (lineId: string, handleIndex: number) => void;
  onMouseMove: (point: Point) => void;
  onMouseUp: (point: Point) => void;
};

const HANDLE_RADIUS = 10;
const LINE_STROKE_WIDTH = 6;
const SELECTED_LINE_STROKE_WIDTH = 9;

function screenToSvg(svg: SVGSVGElement, clientX: number, clientY: number): Point {
  const pt = svg.createSVGPoint();
  pt.x = clientX;
  pt.y = clientY;
  const ctm = svg.getScreenCTM();
  if (!ctm) return [0, 0];
  const transformed = pt.matrixTransform(ctm.inverse());
  return [transformed.x, transformed.y];
}

export default function Viewport({
  model,
  bootstrap,
  videoSize,
  counts,
  onMouseDownEmpty,
  onMouseDownLine,
  onMouseDownHandle,
  onMouseMove,
  onMouseUp,
}: ViewportProps) {
  const svgRef = React.useRef<SVGSVGElement | null>(null);
  const { width, height } = videoSize;
  const showLines = model.visibleLayers['saved-lines'];
  const cursor = getCursor(model);

  const layerCtx: LayerRenderContext = { model, bootstrap, videoSize };

  function handleMouseDown(e: React.MouseEvent<SVGSVGElement>) {
    if (!svgRef.current) return;
    const point = screenToSvg(svgRef.current, e.clientX, e.clientY);
    onMouseDownEmpty(point);
  }

  function handleMouseMove(e: React.MouseEvent<SVGSVGElement>) {
    if (!svgRef.current) return;
    const point = screenToSvg(svgRef.current, e.clientX, e.clientY);
    onMouseMove(point);
  }

  function handleMouseUp(e: React.MouseEvent<SVGSVGElement>) {
    if (!svgRef.current) return;
    const point = screenToSvg(svgRef.current, e.clientX, e.clientY);
    onMouseUp(point);
  }

  function handleLineMouseDown(e: React.MouseEvent<SVGElement>, line: LineGeometry) {
    e.stopPropagation();
    if (!svgRef.current) return;
    const point = screenToSvg(svgRef.current, e.clientX, e.clientY);
    onMouseDownLine(line.id, point);
  }

  function handleHandleMouseDown(e: React.MouseEvent<SVGCircleElement>, lineId: string, handleIndex: number) {
    e.stopPropagation();
    onMouseDownHandle(lineId, handleIndex);
  }

  return (
    <div className="viewport-stage" style={{ cursor }}>
      <svg
        ref={svgRef}
        className="viewport-svg"
        viewBox={`0 0 ${width} ${height}`}
        preserveAspectRatio="xMidYMid meet"
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onMouseLeave={(e) => {
          if (model.interaction.kind !== 'idle' && svgRef.current) {
            const point = screenToSvg(svgRef.current, e.clientX, e.clientY);
            onMouseUp(point);
          }
        }}
      >
        {BUILTIN_IMAGE_LAYERS.map((layer) => layer.render(layerCtx))}

        {/* Counting lines */}
        {showLines &&
          model.lines.map((line) => {
            const isSelected = line.id === model.selectedLineId;
            const isDraft =
              model.interaction.kind === 'drawing' && model.interaction.draftLineId === line.id;
            const [a, b] = line.points;
            if (!a || !b) return null;
            const strokeWidth = isSelected ? SELECTED_LINE_STROKE_WIDTH : LINE_STROKE_WIDTH;
            const opacity = isDraft ? 0.7 : 1;
            const dash = isDraft ? '12 6' : undefined;
            const midX = (a[0] + b[0]) / 2;
            const midY = (a[1] + b[1]) / 2;

            return (
              <g key={line.id} className={isSelected ? 'viewport-line selected' : 'viewport-line'}>
                {/* Invisible thick line for easier click target */}
                <line
                  x1={a[0]}
                  y1={a[1]}
                  x2={b[0]}
                  y2={b[1]}
                  stroke="transparent"
                  strokeWidth={24}
                  style={{ cursor: 'grab', pointerEvents: 'stroke' }}
                  onMouseDown={(e) => handleLineMouseDown(e, line)}
                />
                {/* Visible line */}
                <line
                  x1={a[0]}
                  y1={a[1]}
                  x2={b[0]}
                  y2={b[1]}
                  stroke={line.color}
                  strokeWidth={strokeWidth}
                  strokeLinecap="round"
                  strokeDasharray={dash}
                  opacity={opacity}
                  style={{ pointerEvents: 'none' }}
                />
                {/* Label */}
                {(() => {
                  const lineTotal = counts?.per_line?.[line.id]?.total;
                  const labelText = lineTotal != null ? `${line.name} · ${lineTotal}` : line.name;
                  return (
                    <g style={{ pointerEvents: 'none' }}>
                      <text
                        x={midX + 1}
                        y={midY - 13}
                        fill="rgba(0,0,0,0.85)"
                        fontSize={18}
                        fontWeight={600}
                        textAnchor="middle"
                      >
                        {labelText}
                      </text>
                      <text
                        x={midX}
                        y={midY - 14}
                        fill={line.color}
                        fontSize={18}
                        fontWeight={600}
                        textAnchor="middle"
                      >
                        {labelText}
                      </text>
                    </g>
                  );
                })()}
                {/* Per-side direction labels: arrow perpendicular to the line,
                    count text drawn parallel. Hidden while the user is
                    dragging so the moving line stays readable. */}
                {(() => {
                  if (model.interaction.kind !== 'idle') return null;
                  const lc = counts?.per_line?.[line.id];
                  if (!lc) return null;
                  const pos = lc.by_direction?.positive ?? 0;
                  const neg = lc.by_direction?.negative ?? 0;
                  if (pos + neg === 0) return null;

                  const dx = b[0] - a[0];
                  const dy = b[1] - a[1];
                  const len = Math.hypot(dx, dy) || 1;
                  // Server convention (screen coords, Y down): the unit
                  // vector pointing to the "positive" side is (-dy, dx)/|AB|.
                  const nx = -dy / len;
                  const ny = dx / len;

                  // Keep the text reading left-to-right by flipping the
                  // rotation when the line tilts past vertical.
                  let theta = Math.atan2(dy, dx);
                  if (theta > Math.PI / 2 || theta < -Math.PI / 2) theta += Math.PI;
                  const deg = (theta * 180) / Math.PI;

                  const arrowGap = 10;
                  const arrowLen = 16;
                  const textGap = arrowGap + arrowLen + 12;

                  function arrowHead(tipX: number, tipY: number, dirX: number, dirY: number) {
                    const size = 6;
                    // Tip plus the two base corners on the perpendicular axis.
                    const baseX = tipX - dirX * size * 1.4;
                    const baseY = tipY - dirY * size * 1.4;
                    const px = -dirY;
                    const py = dirX;
                    const left = `${baseX + px * size},${baseY + py * size}`;
                    const right = `${baseX - px * size},${baseY - py * size}`;
                    return `${tipX},${tipY} ${left} ${right}`;
                  }

                  function renderSide(side: 1 | -1, count: number) {
                    if (count === 0) return null;
                    const dirX = side * nx;
                    const dirY = side * ny;
                    const startX = midX + dirX * arrowGap;
                    const startY = midY + dirY * arrowGap;
                    const tipX = midX + dirX * (arrowGap + arrowLen);
                    const tipY = midY + dirY * (arrowGap + arrowLen);
                    const textX = midX + dirX * textGap;
                    const textY = midY + dirY * textGap;
                    return (
                      <g key={side} style={{ pointerEvents: 'none' }}>
                        <line
                          className="viewport-side-arrow"
                          x1={startX}
                          y1={startY}
                          x2={tipX}
                          y2={tipY}
                          stroke={line.color}
                        />
                        <polygon
                          className="viewport-side-arrow-head"
                          fill={line.color}
                          points={arrowHead(tipX, tipY, dirX, dirY)}
                        />
                        <text
                          className="viewport-side-count"
                          x={textX}
                          y={textY}
                          fill={line.color}
                          transform={`rotate(${deg} ${textX} ${textY})`}
                        >
                          {count}
                        </text>
                      </g>
                    );
                  }

                  return (
                    <>
                      {renderSide(+1, pos)}
                      {renderSide(-1, neg)}
                    </>
                  );
                })()}
              </g>
            );
          })}

        {/* Endpoint handles for selected line */}
        {(() => {
          const selected = model.lines.find((l) => l.id === model.selectedLineId);
          if (!selected || !showLines) return null;
          return selected.points.map(([x, y], index) => (
            <circle
              key={`${selected.id}-handle-${index}`}
              cx={x}
              cy={y}
              r={HANDLE_RADIUS}
              fill={selected.color}
              stroke="white"
              strokeWidth={3}
              style={{ cursor: 'nwse-resize' }}
              onMouseDown={(e) => handleHandleMouseDown(e, selected.id, index)}
            />
          ));
        })()}
      </svg>
    </div>
  );
}
