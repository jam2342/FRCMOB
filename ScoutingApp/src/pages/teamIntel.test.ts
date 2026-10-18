import { describe, expect, it } from 'vitest';
import { ratingFromIntel } from './teamIntel';

describe('ratingFromIntel', () => {
  it('carries the points behind the rating and leaves unmeasured subscores empty', () => {
    const rating = ratingFromIntel(
      {
        rating: {
          available: true,
          rating_0_100: 96.31,
          robot_level_0_100: 95.2,
          driver_skill_0_100: null,
          subscores: { throughput: 97.1, auto_contribution: 99.4, endgame: 0, consistency: null, results_anchor: null },
          expected_points: { total: 238.4, teleop: 171.2, auto: 67.2, climb: 0 },
          basis: 'this_event',
          matches_here: 12,
          matches_before: 24,
        },
      },
      'frc254',
      254,
      'The Cheesy Poofs',
      true,
    );
    expect(rating?.expected_points).toEqual({ total: 238.4, teleop: 171.2, auto: 67.2, climb: 0 });
    expect(rating?.basis).toBe('this_event');
    expect(rating?.matches_before).toBe(24);
    expect(rating?.driver_skill_0_100).toBeNull();
    // A missing score used to read as an average 50.
    expect(rating?.subscores.consistency).toBeNull();
    expect(rating?.subscores.results_anchor).toBeNull();
    expect(rating?.subscores.endgame).toBe(0);
  });

  it('reads an older rating without points', () => {
    const rating = ratingFromIntel({ rating: { available: true, rating_0_100: 61, subscores: {} } }, 'frc1', 1, null);
    expect(rating?.expected_points).toBeNull();
    expect(rating?.basis).toBeNull();
  });
});
