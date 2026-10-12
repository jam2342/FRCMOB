import { describe, expect, it } from 'vitest';
import type { EventScheduleItem } from '../../api';
import { nextMatchToWarm } from './matchPreload';

const now = Date.UTC(2026, 3, 18, 15, 0, 0);
const match = (key: string, minutes: number, teams: string[], done = false): EventScheduleItem => ({
  match_key: key,
  scheduled_time: Math.round(now / 1000) + minutes * 60,
  predicted_time: null,
  actual_time: null,
  has_time: true,
  is_completed: done,
  red: teams.slice(0, 3).map((team_key) => ({ team_key, team_number: Number(team_key.slice(3)), nickname: null, station: null })),
  blue: teams.slice(3).map((team_key) => ({ team_key, team_number: Number(team_key.slice(3)), nickname: null, station: null })),
} as unknown as EventScheduleItem);

describe('nextMatchToWarm', () => {
  const six = ['frc1', 'frc2', 'frc3', 'frc4', 'frc5', 'frc254'];
  it("picks my team's next unplayed match within 15 minutes", () => {
    const matches = [match('qm9', 40, six), match('qm5', 12, six), match('qm4', 8, ['frc7', 'frc8', 'frc9', 'frc10', 'frc11', 'frc12']), match('qm3', -5, six)];
    expect(nextMatchToWarm(matches, 'FRC254', now)?.match_key).toBe('qm5');
  });

  it('ignores matches too far away, already played, or without my team', () => {
    expect(nextMatchToWarm([match('qm9', 40, six)], 'frc254', now)).toBeNull();
    expect(nextMatchToWarm([match('qm5', 5, six, true)], 'frc254', now)).toBeNull();
    expect(nextMatchToWarm([match('qm5', 5, six)], 'frc118', now)).toBeNull();
  });
});
