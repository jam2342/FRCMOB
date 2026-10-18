import { describe, expect, it } from 'vitest';

import type { PredictedScheduleMatch } from '../../api';
import { matchWinProbability } from './winProbability';

function match(overrides: Partial<PredictedScheduleMatch> = {}): PredictedScheduleMatch {
  const alliance = { teams: [] };
  return {
    match_key: '2026test_qm1',
    comp_level: 'qm',
    set_number: 1,
    match_number: 1,
    scheduled_time: null,
    red: alliance,
    blue: alliance,
    ...overrides,
  };
}

describe('matchWinProbability', () => {
  it('prefers the server prediction and labels a rating-only one honestly', () => {
    const result = matchWinProbability(match({
      prediction: {
        available: true,
        source_label: 'rating_logistic_v1',
        red_win_prob: 0.7,
        blue_win_prob: 0.3,
        favored_alliance: 'red',
        edge_confidence_0_1: 0.4,
      },
    }));
    expect(result).toEqual({ red: 0.7, blue: 0.3, source: 'rating_model', edgeConfidence: 0.4 });
  });

  it('labels the expected-points formula', () => {
    const result = matchWinProbability(match({
      prediction: {
        available: true,
        source_label: 'deterministic_team_model_v3',
        red_win_prob: 0.8,
        blue_win_prob: 0.2,
        favored_alliance: 'red',
        edge_confidence_0_1: 0.6,
      },
    }));
    expect(result?.source).toBe('points_model');
  });

  it('has no number when the server has no prediction', () => {
    expect(matchWinProbability(match())).toBeNull();
    expect(matchWinProbability(null)).toBeNull();
  });
});
