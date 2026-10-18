import { describe, expect, it } from 'vitest';
import { peekFromIntel } from './peekData';

describe('peekFromIntel', () => {
  it('reads rating, fuel rate, climb and the qualification record with rank', () => {
    const peek = peekFromIntel({
      team: { nickname: 'Citrus Circuits' },
      rating: { available: true, rating_0_100: 68.713 },
      analysis: { averages: { fuel_scoring_rate: 144.62, climb_success_prob: 0.15 } },
      tba: { event_status: { qual: { ranking: { rank: 3, record: { wins: 8, losses: 2, ties: 0 } } }, alliance_status_str: '<b>Captain</b> of <b>Alliance 2</b>' } },
    }, 'frc1678');
    expect(peek).toEqual({
      nickname: 'Citrus Circuits', rating: 68.713, fuelRate: '144.6', climb: '15%',
      record: '8-2-0', recordLabel: 'Record · rank 3', status: 'Captain of Alliance 2',
    });
  });

  it('falls back to the playoff record and leaves missing numbers empty, never zero', () => {
    const peek = peekFromIntel({
      rating: { available: false },
      analysis: { averages: {} },
      tba: { event_status: { qual: null, playoff: { record: { wins: 2, losses: 2, ties: 0 } } } },
    }, 'frc254');
    expect(peek.nickname).toBe('Team 254');
    expect(peek.rating).toBeNull();
    expect(peek.fuelRate).toBeNull();
    expect(peek.climb).toBeNull();
    expect(peek).toMatchObject({ record: '2-2-0', recordLabel: 'Playoff record', status: null });
  });
});
