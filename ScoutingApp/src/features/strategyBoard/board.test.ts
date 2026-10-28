import { describe, expect, it, vi } from 'vitest';
import { drawStroke, extendStroke, fraction, keepStroke, startStroke } from './board';

describe('strategy board strokes', () => {
  it('stores points as field fractions, clamped and rounded', () => {
    expect(fraction(150, 600)).toBe(0.25);
    expect(fraction(-20, 600)).toBe(0);
    expect(fraction(700, 600)).toBe(1);
    expect(fraction(1, 3)).toBe(0.333);
    expect(fraction(5, 0)).toBe(0);
  });

  it('a pen keeps points as the finger moves; an arrow or zone keeps only its ends', () => {
    let pen = startStroke('pen', 'ours', [0.1, 0.1]);
    pen = extendStroke(pen, [0.1005, 0.1]); // too close: dropped
    pen = extendStroke(pen, [0.2, 0.2]);
    expect(pen.points).toEqual([[0.1, 0.1], [0.2, 0.2]]);
    let arrow = startStroke('arrow', 'theirs', [0.1, 0.1]);
    arrow = extendStroke(arrow, [0.3, 0.3]);
    arrow = extendStroke(arrow, [0.5, 0.4]);
    expect(arrow.points).toEqual([[0.1, 0.1], [0.5, 0.4]]);
  });

  it('drops a tap that drew no arrow or zone', () => {
    expect(keepStroke(startStroke('zone', 'note', [0.5, 0.5]))).toBe(false);
    expect(keepStroke(extendStroke(startStroke('zone', 'note', [0.5, 0.5]), [0.6, 0.6]))).toBe(true);
    expect(keepStroke(startStroke('pen', 'ours', [0.5, 0.5]))).toBe(true);
  });

  it('draws an arrow with a head and a zone as a filled box', () => {
    const ctx = {
      beginPath: vi.fn(), moveTo: vi.fn(), lineTo: vi.fn(), stroke: vi.fn(), fill: vi.fn(), arc: vi.fn(), closePath: vi.fn(),
      fillRect: vi.fn(), strokeRect: vi.fn(), strokeStyle: '', fillStyle: '', lineWidth: 1, lineCap: 'butt' as CanvasLineCap, lineJoin: 'miter' as CanvasLineJoin, globalAlpha: 1,
    };
    drawStroke(ctx, { tool: 'arrow', color: 'ours', points: [[0, 0], [0.5, 0.5]] }, 800, 400, 'red');
    expect(ctx.stroke).toHaveBeenCalledTimes(1);
    expect(ctx.fill).toHaveBeenCalledTimes(1);
    drawStroke(ctx, { tool: 'zone', color: 'note', points: [[0.5, 0.5], [0.25, 0.25]] }, 800, 400, 'gold');
    expect(ctx.fillRect).toHaveBeenCalledWith(200, 100, 200, 100);
    expect(ctx.globalAlpha).toBe(1);
  });
});
