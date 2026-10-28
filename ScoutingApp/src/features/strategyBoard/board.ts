import type { StrategyStroke } from '../../api';

export type Tool = StrategyStroke['tool'];
export type StrokeColor = StrategyStroke['color'];

// A pen stroke keeps a point only once the finger has moved this far (a field fraction),
// so a slow drag doesn't store hundreds of near-identical points.
const MIN_STEP = 0.004;
export const MAX_STROKES = 150;

export function fraction(value: number, size: number): number {
  if (!size) return 0;
  return Math.round(Math.min(1, Math.max(0, value / size)) * 1000) / 1000;
}

export function startStroke(tool: Tool, color: StrokeColor, point: [number, number]): StrategyStroke {
  return { tool, color, points: tool === 'pen' ? [point] : [point, point] };
}

export function extendStroke(stroke: StrategyStroke, point: [number, number]): StrategyStroke {
  if (stroke.tool !== 'pen') return { ...stroke, points: [stroke.points[0], point] };
  const last = stroke.points[stroke.points.length - 1];
  if (last && Math.hypot(point[0] - last[0], point[1] - last[1]) < MIN_STEP) return stroke;
  return { ...stroke, points: [...stroke.points, point] };
}

// An arrow or zone with no size was a tap, not a drawing.
export function keepStroke(stroke: StrategyStroke): boolean {
  if (stroke.tool === 'pen') return stroke.points.length > 0;
  const [a, b] = stroke.points;
  return Boolean(a && b) && Math.hypot(b[0] - a[0], b[1] - a[1]) >= 0.02;
}

type Ctx = Pick<CanvasRenderingContext2D, 'beginPath' | 'moveTo' | 'lineTo' | 'stroke' | 'fill' | 'arc' | 'closePath' | 'fillRect' | 'strokeRect'> & {
  strokeStyle: string | CanvasGradient | CanvasPattern;
  fillStyle: string | CanvasGradient | CanvasPattern;
  lineWidth: number;
  lineCap: CanvasLineCap;
  lineJoin: CanvasLineJoin;
  globalAlpha: number;
};

export function drawStroke(ctx: Ctx, stroke: StrategyStroke, w: number, h: number, colour: string): void {
  const pts = stroke.points.map(([x, y]) => [x * w, y * h] as const);
  if (!pts.length) return;
  ctx.strokeStyle = colour;
  ctx.fillStyle = colour;
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';
  ctx.lineWidth = Math.max(3, w / 160);
  if (stroke.tool === 'zone') {
    const [[x1, y1], [x2, y2]] = [pts[0], pts[pts.length - 1]];
    ctx.globalAlpha = 0.22;
    ctx.fillRect(Math.min(x1, x2), Math.min(y1, y2), Math.abs(x2 - x1), Math.abs(y2 - y1));
    ctx.globalAlpha = 1;
    ctx.strokeRect(Math.min(x1, x2), Math.min(y1, y2), Math.abs(x2 - x1), Math.abs(y2 - y1));
    return;
  }
  if (pts.length === 1) {
    ctx.beginPath();
    ctx.arc(pts[0][0], pts[0][1], ctx.lineWidth, 0, Math.PI * 2);
    ctx.fill();
    return;
  }
  ctx.beginPath();
  ctx.moveTo(pts[0][0], pts[0][1]);
  for (const [x, y] of pts.slice(1)) ctx.lineTo(x, y);
  ctx.stroke();
  if (stroke.tool === 'arrow') {
    const [fx, fy] = pts[pts.length - 2];
    const [tx, ty] = pts[pts.length - 1];
    const angle = Math.atan2(ty - fy, tx - fx);
    const head = ctx.lineWidth * 4;
    ctx.beginPath();
    ctx.moveTo(tx, ty);
    ctx.lineTo(tx - head * Math.cos(angle - Math.PI / 7), ty - head * Math.sin(angle - Math.PI / 7));
    ctx.lineTo(tx - head * Math.cos(angle + Math.PI / 7), ty - head * Math.sin(angle + Math.PI / 7));
    ctx.closePath();
    ctx.fill();
  }
}
