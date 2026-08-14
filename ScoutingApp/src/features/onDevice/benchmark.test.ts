import { describe, expect, it } from 'vitest';

import { benchmark, summarizeTimings } from './benchmark';

describe('benchmark harness', () => {
  it('summarizeTimings computes median, p90, max, fps', () => {
    const s = summarizeTimings([10, 20, 30, 40, 100]);
    expect(s.iterations).toBe(5);
    expect(s.msMedian).toBe(30);
    expect(s.msMax).toBe(100);
    expect(s.fps).toBeCloseTo(1000 / 30, 4);
    expect(s.msP90).toBeGreaterThanOrEqual(40);
  });

  it('summarizeTimings handles empty input', () => {
    expect(summarizeTimings([])).toMatchObject({ iterations: 0, fps: 0 });
  });

  it('benchmark runs warmup + iterations and reports a summary', async () => {
    let calls = 0;
    const run = async () => {
      calls += 1;
    };
    const result = await benchmark(run, { iterations: 10, warmup: 2 });
    expect(calls).toBe(12); // warmup + iterations
    expect(result.iterations).toBe(10);
    expect(result.fps).toBeGreaterThan(0);
    expect(typeof result.thermalDriftPct).toBe('number');
  });
});
