import { useCallback, useEffect, useRef } from 'react';
import type { TeamHeatmapResponse } from '../../api';
import { useCanvasTokens } from '../../hooks/useCanvasTokens';
import './FieldHeatmap.css';

/* The field's own colours. The heat gradient below is deliberately NOT in here:
   it is a scale, and a scale that changed between themes would stop meaning the
   same thing from one screenshot to the next. */
const FIELD_TOKENS = [
  '--field-canvas-bg',
  '--field-canvas-grid',
  '--field-canvas-line',
  '--field-canvas-red',
  '--field-canvas-blue',
] as const;

/* ── constants ────────────────────────────────────────────────────── */

/** Default FRC field dimensions (metres) — used as fallback. */
const DEFAULT_FIELD_W = 16.541;
const DEFAULT_FIELD_H = 8.0693;

/** Heat colour stops — from cold (blue) to hot (red). */
const HEAT_STOPS: [number, number, number, number][] = [
  // [r, g, b, a]  at threshold breakpoints 0.0 → 1.0
  [59, 130, 246, 0],      // transparent blue
  [59, 130, 246, 0.35],   // cool blue
  [34, 197, 94, 0.50],    // green
  [234, 179, 8, 0.70],    // yellow
  [239, 68, 68, 0.90],    // hot red
];

function lerpColor(t: number): [number, number, number, number] {
  const n = HEAT_STOPS.length - 1;
  const idx = Math.min(Math.floor(t * n), n - 1);
  const local = (t * n) - idx;
  const a = HEAT_STOPS[idx];
  const b = HEAT_STOPS[idx + 1];
  return [
    Math.round(a[0] + (b[0] - a[0]) * local),
    Math.round(a[1] + (b[1] - a[1]) * local),
    Math.round(a[2] + (b[2] - a[2]) * local),
    a[3] + (b[3] - a[3]) * local,
  ];
}

/* ── types ────────────────────────────────────────────────────────── */

type Props = {
  data: TeamHeatmapResponse;
  className?: string;
  /** Show the grid lines and zone overlays for spatial context. */
  showGrid?: boolean;
};

/* ── component ────────────────────────────────────────────────────── */

export function FieldHeatmap({ data, className, showGrid = true }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  // A new object on theme change, so `draw` changes identity and the effect
  // below repaints — a canvas keeps its pixels when the stylesheet swaps.
  const tokens = useCanvasTokens(FIELD_TOKENS);

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    const container = containerRef.current;
    if (!canvas || !container) return;

    const fieldW = data.field_length_m || DEFAULT_FIELD_W;
    const fieldH = data.field_width_m || DEFAULT_FIELD_H;

    const w = container.clientWidth;
    const h = w * (fieldH / fieldW);
    const dpr = window.devicePixelRatio || 1;

    canvas.width = w * dpr;
    canvas.height = h * dpr;
    canvas.style.width = `${w}px`;
    canvas.style.height = `${h}px`;

    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    ctx.scale(dpr, dpr);

    /* ── background ──────────────────────────────────────────── */
    ctx.fillStyle = tokens['--field-canvas-bg'];
    ctx.fillRect(0, 0, w, h);

    /* ── grid lines ──────────────────────────────────────────── */
    if (showGrid) {
      ctx.strokeStyle = tokens['--field-canvas-grid'];
      ctx.lineWidth = 1;
      for (let mx = 1; mx < fieldW; mx++) {
        const x = (mx / fieldW) * w;
        ctx.beginPath();
        ctx.moveTo(x, 0);
        ctx.lineTo(x, h);
        ctx.stroke();
      }
      for (let my = 1; my < fieldH; my++) {
        const y = (1 - my / fieldH) * h;
        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.lineTo(w, y);
        ctx.stroke();
      }

      /* field border */
      ctx.strokeStyle = tokens['--field-canvas-line'];
      ctx.lineWidth = 2;
      ctx.strokeRect(1, 1, w - 2, h - 2);

      /* centre line */
      ctx.strokeStyle = tokens['--field-canvas-grid'];
      ctx.lineWidth = 1;
      ctx.setLineDash([6, 4]);
      const cx = w / 2;
      ctx.beginPath();
      ctx.moveTo(cx, 0);
      ctx.lineTo(cx, h);
      ctx.stroke();
      ctx.setLineDash([]);
    }

    /* ── heatmap cells ───────────────────────────────────────── */
    const { grid, grid_cols, grid_rows } = data;
    if (!grid || grid.length === 0) return;

    const cellW = w / grid_cols;
    const cellH = h / grid_rows;

    for (let r = 0; r < grid_rows; r++) {
      for (let c = 0; c < grid_cols; c++) {
        const val = grid[r]?.[c] ?? 0;
        if (val < 0.01) continue; // skip near-zero cells for perf

        const [cr, cg, cb, ca] = lerpColor(val);
        ctx.fillStyle = `rgba(${cr},${cg},${cb},${ca})`;

        // Row 0 = bottom of field (field_y=0), flip y for canvas
        const px = c * cellW;
        const py = (grid_rows - 1 - r) * cellH;
        ctx.fillRect(px, py, cellW + 0.5, cellH + 0.5); // +0.5 prevents hairline gaps
      }
    }

    /* ── alliance labels ─────────────────────────────────────── */
    ctx.font = `bold ${Math.max(10, w * 0.015)}px Inter, sans-serif`;
    ctx.textBaseline = 'top';

    ctx.fillStyle = tokens['--field-canvas-red'];
    ctx.fillText('RED', 6, 6);

    ctx.fillStyle = tokens['--field-canvas-blue'];
    const blueMetrics = ctx.measureText('BLUE');
    ctx.fillText('BLUE', w - blueMetrics.width - 6, 6);
  }, [data, showGrid, tokens]);

  /* ── draw on mount and resize ───────────────────────────────── */
  useEffect(() => {
    draw();
    const onResize = () => draw();
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, [draw]);

  /* ── empty state ────────────────────────────────────────────── */
  if (data.total_points === 0) {
    return (
      <div className={`field-heatmap ${className ?? ''}`}>
        <div className="field-heatmap__empty">
          <div className="field-heatmap__empty-title">No Position Data</div>
          <div>This team has no field-coordinate tracking data for this event yet.</div>
        </div>
      </div>
    );
  }

  return (
    <div className={`field-heatmap ${className ?? ''}`} ref={containerRef}>
      <canvas ref={canvasRef} className="field-heatmap__canvas" />

      {/* colour legend */}
      <div className="field-heatmap__legend">
        <span className="field-heatmap__legend-label">Low</span>
        <div className="field-heatmap__legend-gradient" />
        <span className="field-heatmap__legend-label">High</span>
      </div>

      {/* stats row */}
      <div className="field-heatmap__stats">
        <span>
          Points: <span className="field-heatmap__stat-value">{data.total_points.toLocaleString()}</span>
        </span>
        <span>
          Matches: <span className="field-heatmap__stat-value">{data.match_count}</span>
        </span>
        <span>
          Resolution: <span className="field-heatmap__stat-value">{data.grid_cols}×{data.grid_rows}</span>
        </span>
      </div>
    </div>
  );
}
