import { describe, expect, it } from 'vitest';

import type { ScheduleWithSynergyMatch } from '../../api';
import { matchWinProbability, synergyWinProbability } from './winProbability';

function match(overrides: Partial<ScheduleWithSynergyMatch> = {}): ScheduleWithSynergyMatch {
  const alliance = (score: number) => ({
    teams: [],
    synergy: { alliance_synergy_score_0_100: score } as ScheduleWithSynergyMatch['red']['synergy'],
  });
  return {
    match_key: '2026test_qm1',
    comp_level: 'qm',
    set_number: 1,
    match_number: 1,
    scheduled_time: null,
    red: alliance(60),
    blue: alliance(45),
    ...overrides,
  };
}

describe('matchWinProbability', () => {
  it('prefers the server prediction and labels a rating-only one honestly', () => {
    const result = matchWinProbability(match({
      prediction: {
        available: true,
        source_label: 'rating_logistic_v1',
        model_key: 'match_outcome',
        model_version: null,
        red_win_prob: 0.7,
        blue_win_prob: 0.3,
        favored_alliance: 'red',
        edge_confidence_0_1: 0.4,
      },
    }));
    expect(result).toEqual({ red: 0.7, blue: 0.3, source: 'rating_model', edgeConfidence: 0.4 });
  });

  it('labels the fuel-rate formula', () => {
    const result = matchWinProbability(match({
      prediction: {
        available: true,
        source_label: 'deterministic_fuel_v1',
        model_key: 'match_outcome',
        model_version: null,
        red_win_prob: 0.8,
        blue_win_prob: 0.2,
        favored_alliance: 'red',
        edge_confidence_0_1: 0.6,
      },
    }));
    expect(result?.source).toBe('fuel_model');
  });

  it('marks an ML-blended prediction as ML', () => {
    const result = matchWinProbability(match({
      prediction: {
        available: true,
        source_label: 'blended_det_ml_v1',
        model_key: 'match_outcome',
        model_version: 'v3',
        red_win_prob: 0.55,
        blue_win_prob: null,
        favored_alliance: 'red',
        edge_confidence_0_1: null,
      },
    }));
    expect(result?.source).toBe('ml_model');
    expect(result?.blue).toBeCloseTo(0.45);
  });

  it('falls back to the synergy gap when the server has no prediction', () => {
    const result = matchWinProbability(match());
    expect(result?.source).toBe('synergy_derived');
    expect(result?.red).toBeCloseTo(synergyWinProbability(60, 45)!);
    expect(result!.red + result!.blue).toBeCloseTo(1);
  });

  it('returns null without either signal', () => {
    expect(synergyWinProbability(null, 40)).toBeNull();
    expect(matchWinProbability(null)).toBeNull();
  });
});
