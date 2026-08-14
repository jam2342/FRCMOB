import { describe, expect, it } from 'vitest';
import { parseAllianceSlotTeam } from './eventsPage.helpers';

describe('parseAllianceSlotTeam', () => {
  it('accepts case-variant FIRST payload keys', () => {
    expect(
      parseAllianceSlotTeam({
        TeamNumber: 1678,
        TeamName: 'Citrus Circuits',
      }),
    ).toEqual({
      teamNumber: 1678,
      name: 'Citrus Circuits',
    });
  });

  it('parses team key strings when numeric field is missing', () => {
    expect(
      parseAllianceSlotTeam({
        team_key: 'frc254',
        team_name: 'The Cheesy Poofs',
      }),
    ).toEqual({
      teamNumber: 254,
      name: 'The Cheesy Poofs',
    });
  });

  it('accepts scalar team-number slots', () => {
    expect(parseAllianceSlotTeam(1690)).toEqual({
      teamNumber: 1690,
      name: '',
    });
  });

  it('accepts scalar team-key slots', () => {
    expect(parseAllianceSlotTeam('frc2056')).toEqual({
      teamNumber: 2056,
      name: '',
    });
  });

  it('returns null when no valid team identifier is present', () => {
    expect(parseAllianceSlotTeam({ name: 'Unknown team' })).toBeNull();
  });
});
