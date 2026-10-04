import { describe, expect, it } from 'vitest';

import { suggestionGroups, suggestionsFor, type SuggestPath } from './pathSuggestions';

function path(pathId: number, t0: number, t1: number, x0: number, x1: number, alliance: SuggestPath['alliance'] = null, y = 4): SuggestPath {
  const points = [];
  for (let t = t0; t <= t1 + 1e-9; t += 0.5) points.push({ timeSec: t, fieldX: x0 + ((x1 - x0) * (t - t0)) / Math.max(1e-9, t1 - t0), fieldY: y });
  return { pathId, points, alliance };
}

describe('suggestionGroups', () => {
  it('chains a robot that disappears for a few seconds and reappears nearby', () => {
    const groups = suggestionGroups([path(1, 0, 5, 2, 4), path(2, 9, 12, 5, 7), path(3, 15, 18, 8, 9)]);
    expect(groups).toEqual([[1, 2, 3]]);
  });

  it('never chains across bumper colours or impossible distances', () => {
    expect(suggestionGroups([path(1, 0, 5, 2, 4, 'red'), path(2, 7, 9, 4.5, 6, 'blue')])).toEqual([[1], [2]]);
    expect(suggestionGroups([path(1, 0, 5, 2, 4), path(2, 6, 9, 14, 15)])).toEqual([[1], [2]]);
  });

  it('suggests nothing when two continuations are about equally likely', () => {
    const groups = suggestionGroups([path(1, 0, 5, 2, 4), path(2, 7, 9, 5, 6, null, 4), path(3, 7, 9, 5, 6, null, 4.4)]);
    expect(groups.find((g) => g.includes(1))).toEqual([1]);
  });
});

describe('suggestionsFor', () => {
  const paths = [path(1, 0, 5, 2, 4), path(2, 9, 12, 5, 7), path(3, 15, 18, 8, 9)];
  const groups = suggestionGroups(paths);

  it('offers the unassigned rest of the chain', () => {
    expect(suggestionsFor(paths, groups, 1, 'frc254', { 1: 'frc254' })).toEqual([2, 3]);
    expect(suggestionsFor(paths, groups, 1, 'frc254', { 1: 'frc254', 3: 'frc1678' })).toEqual([2]);
  });

  it('never puts one team in two places at once', () => {
    const withOverlap = [...paths, path(4, 10, 11, 12, 13)];
    expect(suggestionsFor(withOverlap, groups, 1, 'frc254', { 1: 'frc254', 4: 'frc254' })).toEqual([3]);
  });
});
