import React from 'react';
import type { LineGeometry, OverlayModel, Point, VideoSize } from './viewportState';

type ViewportProps = {
  model: OverlayModel;
  videoSize: VideoSize;
  frameUrl?: string;
  trajectoriesUrl?: string;
  heatmapUrl?: string;
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
  videoSize,
  frameUrl,
  trajectoriesUrl,
  heatmapUrl,
  onMouseDownEmpty,
  onMouseDownLine,
  onMouseDownHandle,
  onMouseMove,
  onMouseUp,
}: ViewportProps) {
  const svgRef = React.useRef<SVGSVGElement | null>(null);
  const { width, height } = videoSize;
  const showTraj = model.visibleLayers['trajectories'] && trajectoriesUrl;
  const showHeatmap = model.visibleLayers['heatmap'] && heatmapUrl;
  const showLines = model.visibleLayers['saved-lines'];

  // Cursor styling per interaction state
  let cursor: React.CSSProperties['cursor'] = 'crosshair';
  if (model.interaction.kind === 'moving') cursor = 'grabbing';
  else if (model.interaction.kind === 'resizing') cursor = 'nwse-resize';
  else if (model.interaction.kind === 'drawing') cursor = 'crosshair';

  function handleMouseDown(e: React.MouseEvent<SVGSVGElement>) {
    if (!svgRef.current) return;
    // Stop only when clicking on empty background — let line/handle handlers fire first.
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
        {/* Base frame */}
        {frameUrl ? (
          <image
            href={frameUrl}
            x={0}
            y={0}
            width={width}
            height={height}
            preserveAspectRatio="none"
            style={{ pointerEvents: 'none' }}
          />
        ) : (
          <rect width={width} height={height} fill="#0e1424" />
        )}

        {/* Trajectories overlay */}
        {showTraj ? (
          <image
            href={trajectoriesUrl}
            x={0}
            y={0}
            width={width}
            height={height}
            preserveAspectRatio="none"
            style={{ pointerEvents: 'none', opacity: 0.85 }}
          />
        ) : null}

        {/* Heatmap overlay */}
        {showHeatmap ? (
          <image
            href={heatmapUrl}
            x={0}
            y={0}
            width={width}
            height={height}
            preserveAspectRatio="none"
            style={{ pointerEvents: 'none', mixBlendMode: 'screen' as React.CSSProperties['mixBlendMode'] }}
          />
        ) : null}

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
                <g style={{ pointerEvents: 'none' }}>
                  <text
                    x={midX + 1}
                    y={midY - 13}
                    fill="rgba(0,0,0,0.85)"
                    fontSize={18}
                    fontWeight={600}
                    textAnchor="middle"
                  >
                    {line.name}
                  </text>
                  <text
                    x={midX}
                    y={midY - 14}
                    fill={line.color}
                    fontSize={18}
                    fontWeight={600}
                    textAnchor="middle"
                  >
                    {line.name}
                  </text>
                </g>
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
