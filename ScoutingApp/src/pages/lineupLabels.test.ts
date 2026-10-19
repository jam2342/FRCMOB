import { describe, expect, it } from 'vitest';
import { compactAllianceLabel } from './eventCalendar';
import { teamDisplayNumber } from './centerUtils';

describe('lineup labels', () => {
  it('names a B team by its key instead of "#0"', () => {
    expect(teamDisplayNumber({ team_key: 'frc4159B', team_number: 0 })).toBe('4159B');
    expect(teamDisplayNumber({ team_key: 'frc254', team_number: 254 })).toBe('254');
  });

  it('lists every robot on the alliance, not two and "+1"', () => {
    const label = compactAllianceLabel([
      { team_key: 'frc841', team_number: 841 },
      { team_key: 'frc100B', team_number: 0 },
      { team_key: 'frc670', team_number: 670 },
    ] as never);
    expect(label).toBe('#841 · #100B · #670');
  });
});
