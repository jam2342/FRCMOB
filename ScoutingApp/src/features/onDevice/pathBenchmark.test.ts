import { describe, expect, it } from 'vitest';

import { scoreAgainstLabels, scorePaths } from './pathBenchmark';
import { type TrackPoint } from './trackProduction';

function path(times: number[], x: number): TrackPoint[] {
  return times.map((t) => ({ timeSec: t, fieldX: x, fieldY: 4, zoneKey: null, speedMps: null }));
}

const T = [0, 1, 2, 3, 4, 5];

describe('scorePaths', () => {
  it('scores a perfect breakdown as full coverage, purity and one tap per robot', () => {
    const ref = { 0: path(T, 2), 1: path(T, 8) };
    const s = scorePaths({ 5: path(T, 2.1), 6: path(T, 8) }, ref, T);
    expect(s.coverage).toBe(1);
    expect(s.purity).toBe(1);
    expect(s.attributed).toBe(1);
    expect(s.tapsFor90).toBe(2);
    expect(s.medianErrorM).toBeCloseTo(0.1, 5);
  });

  it('charges a path that swaps robots halfway through', () => {
    const ref = { 0: path(T, 2), 1: path(T, 8) };
    const swapped = [...path([0, 1, 2], 2), ...path([3, 4, 5], 8)];
    const s = scorePaths({ 1: swapped, 2: [...path([0, 1, 2], 8), ...path([3, 4, 5], 2)] }, ref, T);
    expect(s.coverage).toBe(1);
    expect(s.purity).toBe(0.5);
  });

  it('counts fragments as extra taps and misses as lost coverage', () => {
    const ref = { 0: path(T, 2) };
    const s = scorePaths({ 1: path([0, 1], 2), 2: path([2, 3], 2) }, ref, T);
    expect(s.coverage).toBeCloseTo(4 / 6);
    expect(s.tapsFor90).toBe(2);
  });
});

describe('scoreAgainstLabels', () => {
  it('credits each path with its majority team and flags paths carrying two teams', () => {
    const paths = { 1: path(T, 2), 2: [...path([0, 1, 2], 8), ...path([3, 4, 5], 12)] };
    const labels = [
      { timeSec: 1, fieldX: 2, fieldY: 4, team: 'frc1' },
      { timeSec: 4, fieldX: 2, fieldY: 4, team: 'frc1' },
      { timeSec: 1, fieldX: 8, fieldY: 4, team: 'frc2' },
      { timeSec: 4, fieldX: 12, fieldY: 4, team: 'frc3' },
      { timeSec: 4, fieldX: 30, fieldY: 4, team: 'frc4' }, // no path there
    ];
    const s = scoreAgainstLabels(paths, labels);
    expect(s.found).toBe(4);
    expect(s.correct).toBe(3);
    expect(s.mixedPaths).toBe(1);
  });
});
