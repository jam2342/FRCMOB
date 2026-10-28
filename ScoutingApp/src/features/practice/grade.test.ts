import { describe, expect, it } from 'vitest';
import type { MatchPracticeResponse } from '../../api';
import { gradePractice, practiceScore, type PracticeAnswers } from './grade';

const practice = (official: Partial<MatchPracticeResponse['official']>): MatchPracticeResponse => ({
  ok: true, match_key: '2026mndu_qm7', event_key: '2026mndu', label: 'QM 7', team_key: 'frc22', alliance: 'blue',
  played: true, checkable: true, video: null,
  official: { auto_tower_points: 15, endgame_tower_points: 20, alliance_fuel: 50, alliance_score: 140, ...official },
});
const answers = (patch: Partial<PracticeAnswers> = {}): PracticeAnswers => ({ autoFuel: 6, teleopFuel: 14, autoClimb: true, endgame: 'climb_level_2', ...patch });

describe('gradePractice', () => {
  it('marks climbs right or wrong and fuel as plausible', () => {
    const checks = gradePractice(answers(), practice({}));
    expect(checks.map((c) => [c.id, c.verdict])).toEqual([['auto-climb', 'right'], ['endgame', 'right'], ['fuel', 'plausible']]);
    expect(checks[2].note).toBe('That’s 40% of the alliance’s fuel. Only the alliance total is official, so this can’t be checked exactly.');
    expect(practiceScore(checks)).toEqual({ right: 2, checked: 2 });
  });

  it('says what the official result had when the scout got it wrong', () => {
    const checks = gradePractice(answers({ autoClimb: false, endgame: 'climb_level_3', teleopFuel: 60 }), practice({}));
    expect(checks[0]).toMatchObject({ verdict: 'off', yours: 'No climb', official: 'Climbed' });
    expect(checks[1]).toMatchObject({ verdict: 'off', yours: 'Climbed 3 levels', official: 'Climbed 2 levels' });
    expect(checks[2]).toMatchObject({ verdict: 'off', yours: '66' });
    expect(practiceScore(checks)).toEqual({ right: 0, checked: 3 });
  });

  it('treats parking as not climbing, and leaves out what the result lacks', () => {
    const checks = gradePractice(answers({ endgame: 'parked', autoClimb: false }), practice({ endgame_tower_points: 0, auto_tower_points: null, alliance_fuel: null }));
    expect(checks[1]).toMatchObject({ verdict: 'right', official: 'No climb' });
    expect(checks[1].note).toMatch(/Parking/);
    expect(checks[0].verdict).toBe('unknown');
    expect(checks[2].verdict).toBe('unknown');
    expect(practiceScore(checks)).toEqual({ right: 1, checked: 1 });
  });
});
