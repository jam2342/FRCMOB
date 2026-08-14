import { describe, expect, it } from 'vitest';

import { classifyZone, pointInPolygon } from './fieldZones';

describe('field zones', () => {
  it('classifies known zone centers', () => {
    expect(classifyZone(11.902, 4.021)).toBe('red_alliance_scoring_zone');
    expect(classifyZone(4.611, 4.021)).toBe('blue_alliance_scoring_zone');
    expect(classifyZone(14.839, 7.168)).toBe('red_loading_depot_zone');
    expect(classifyZone(8.271, 4.035)).toBe('neutral_transition_zone');
  });

  it('returns null outside any zone', () => {
    expect(classifyZone(-5, -5)).toBeNull();
    expect(classifyZone(100, 100)).toBeNull();
  });

  it('pointInPolygon basics', () => {
    const square: [number, number][] = [[0, 0], [10, 0], [10, 10], [0, 10]];
    expect(pointInPolygon(5, 5, square)).toBe(true);
    expect(pointInPolygon(15, 5, square)).toBe(false);
  });
});
