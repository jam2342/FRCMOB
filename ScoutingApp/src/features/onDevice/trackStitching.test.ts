import { describe, expect, it } from 'vitest';

import { stitchTracklets, trackletAlliance, type Tracklet } from './trackStitching';

function line(id: number, t0: number, t1: number, x0: number, x1: number, colour = { red: 0, blue: 0 }): Tracklet {
  const points = [];
  for (let t = t0; t <= t1 + 1e-9; t += 0.5) {
    points.push({ timeSec: t, x: x0 + ((x1 - x0) * (t - t0)) / Math.max(1e-9, t1 - t0), y: 4 });
  }
  return { id, points, ...colour };
}

const RED = { red: 0.2, blue: 0.01 };
const BLUE = { red: 0.01, blue: 0.2 };

describe('trackletAlliance', () => {
  it('needs one colour to clearly dominate', () => {
    expect(trackletAlliance(RED)).toBe('red');
    expect(trackletAlliance(BLUE)).toBe('blue');
    expect(trackletAlliance({ red: 0.1, blue: 0.08 })).toBeNull();
    expect(trackletAlliance({ red: 0.01, blue: 0 })).toBeNull();
  });
});

describe('stitchTracklets', () => {
  it('joins a robot that vanished for a moment and reappeared nearby', () => {
    const map = stitchTracklets([line(1, 0, 5, 2, 4), line(2, 6.5, 10, 5, 7)]);
    expect(map.get(2)).toBe(map.get(1));
  });

  it('refuses a gap the robot could not have driven', () => {
    const map = stitchTracklets([line(1, 0, 5, 2, 4), line(2, 5.5, 8, 12, 13)]);
    expect(map.get(2)).not.toBe(map.get(1));
  });

  it('refuses to join a red fragment onto a blue one', () => {
    const map = stitchTracklets([line(1, 0, 5, 2, 4, RED), line(2, 6, 9, 4.5, 6, BLUE)]);
    expect(map.get(2)).not.toBe(map.get(1));
  });

  it('gives a fragment one successor: the closer of two candidates', () => {
    const map = stitchTracklets([line(1, 0, 5, 2, 4), line(2, 6, 9, 4.3, 6), line(3, 6, 9, 6.5, 8)]);
    expect(map.get(2)).toBe(map.get(1));
    expect(map.get(3)).not.toBe(map.get(1));
  });

  it('never joins fragments that overlap in time', () => {
    const map = stitchTracklets([line(1, 0, 5, 2, 4), line(2, 4, 9, 4, 6)]);
    expect(map.get(2)).not.toBe(map.get(1));
  });

  it('keeps a chain one colour even when each link alone looks fine', () => {
    // red -> unknown is allowed, but the chain is now red, so -> blue is refused
    const map = stitchTracklets([line(1, 0, 4, 2, 3, RED), line(2, 5, 8, 3.2, 4), line(3, 9, 12, 4.2, 5, BLUE)]);
    expect(map.get(2)).toBe(map.get(1));
    expect(map.get(3)).not.toBe(map.get(1));
  });
});
