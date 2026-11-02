import { describe, expect, it } from 'vitest';
import { teamDisplayNumber } from './centerUtils';

describe('lineup labels', () => {
  it('names a B team by its key instead of "#0"', () => {
    expect(teamDisplayNumber({ team_key: 'frc4159B', team_number: 0 })).toBe('4159B');
    expect(teamDisplayNumber({ team_key: 'frc254', team_number: 254 })).toBe('254');
  });
});
