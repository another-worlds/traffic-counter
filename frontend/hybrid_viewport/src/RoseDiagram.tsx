import type { TrackStats } from './viewportState';

type RoseDiagramProps = {
  bins?: TrackStats['direction_bins'];
  size?: number;
};

const DIRECTIONS: { key: 'right' | 'up' | 'left' | 'down'; angle: number; label: string }[] = [
  { key: 'right', angle: 0, label: '→' },
  { key: 'up', angle: -90, label: '↑' },
  { key: 'left', angle: 180, label: '←' },
  { key: 'down', angle: 90, label: '↓' },
];

function polar(cx: number, cy: number, r: number, deg: number): [number, number] {
  const rad = (deg * Math.PI) / 180;
  return [cx + r * Math.cos(rad), cy + r * Math.sin(rad)];
}

function wedgePath(cx: number, cy: number, r: number, startDeg: number, endDeg: number): string {
  const [x0, y0] = polar(cx, cy, r, startDeg);
  const [x1, y1] = polar(cx, cy, r, endDeg);
  const largeArc = Math.abs(endDeg - startDeg) > 180 ? 1 : 0;
  return `M ${cx} ${cy} L ${x0.toFixed(2)} ${y0.toFixed(2)} A ${r} ${r} 0 ${largeArc} 1 ${x1.toFixed(2)} ${y1.toFixed(2)} Z`;
}

export default function RoseDiagram({ bins, size = 220 }: RoseDiagramProps) {
  const safeBins = {
    right: bins?.right ?? 0,
    left: bins?.left ?? 0,
    up: bins?.up ?? 0,
    down: bins?.down ?? 0,
  };
  const total = safeBins.right + safeBins.left + safeBins.up + safeBins.down;
  const maxR = size / 2 - 18;
  const cx = size / 2;
  const cy = size / 2;

  // Each direction occupies a 90° wedge centred on its angle.
  // We scale the wedge radius proportional to the count vs. the maximum.
  const maxCount = Math.max(safeBins.right, safeBins.left, safeBins.up, safeBins.down, 1);

  return (
    <div className="rose-container">
      <svg
        className="rose-svg"
        viewBox={`0 0 ${size} ${size}`}
        width="100%"
        style={{ maxWidth: size }}
        aria-label="Direction rose diagram"
      >
        {/* Background rings */}
        {[0.25, 0.5, 0.75, 1].map((frac) => (
          <circle
            key={frac}
            cx={cx}
            cy={cy}
            r={maxR * frac}
            fill="none"
            stroke="rgba(255,255,255,0.08)"
            strokeWidth={1}
          />
        ))}

        {/* Crosshairs */}
        <line x1={cx - maxR} y1={cy} x2={cx + maxR} y2={cy} stroke="rgba(255,255,255,0.08)" strokeWidth={1} />
        <line x1={cx} y1={cy - maxR} x2={cx} y2={cy + maxR} stroke="rgba(255,255,255,0.08)" strokeWidth={1} />

        {/* Wedges */}
        {DIRECTIONS.map((d) => {
          const value = safeBins[d.key];
          const r = (value / maxCount) * maxR;
          if (r < 1) return null;
          const startDeg = d.angle - 45;
          const endDeg = d.angle + 45;
          return (
            <path
              key={d.key}
              d={wedgePath(cx, cy, r, startDeg, endDeg)}
              fill={d.key === 'right' || d.key === 'left' ? '#4ecdc4' : '#f7b731'}
              fillOpacity={0.65}
              stroke="rgba(255,255,255,0.4)"
              strokeWidth={1}
            />
          );
        })}

        {/* Direction labels */}
        {DIRECTIONS.map((d) => {
          const [lx, ly] = polar(cx, cy, maxR + 10, d.angle);
          return (
            <text
              key={`label-${d.key}`}
              x={lx}
              y={ly + 5}
              textAnchor="middle"
              className="rose-label"
              fill="#f4f7fb"
              fontSize={16}
              fontWeight={600}
            >
              {d.label}
            </text>
          );
        })}
      </svg>
      <div className="rose-legend">
        {DIRECTIONS.map((d) => {
          const v = safeBins[d.key];
          const pct = total > 0 ? Math.round((100 * v) / total) : 0;
          return (
            <div key={d.key} className="rose-legend-row">
              <span className="rose-legend-arrow">{d.label}</span>
              <span className="rose-legend-val">{v}</span>
              <span className="rose-legend-pct">{pct}%</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
