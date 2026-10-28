import { describe, expect, it } from 'vitest';
import { liveFlagRequest, liveFlagToast, parseLiveFlag } from './liveFlags';

describe('live flags', () => {
  it('sends the tag, team and match', () => {
    expect(JSON.parse(liveFlagRequest('broke_down', 'frc254', '2026mndu_qm12')!)).toEqual({
      type: 'flag', tag: 'broke_down', team_key: 'frc254', match_key: '2026mndu_qm12',
    });
    expect(liveFlagRequest('tipped', '', '2026mndu_qm12')).toBeNull();
  });

  it('reads a flag from the room and words it for a leader', () => {
    const flag = parseLiveFlag({ type: 'live_flag', scout_profile: 'Ava', team_key: 'FRC254', match_key: '2026mndu_qm12', tag: 'broke_down' });
    expect(flag).toEqual({ scout: 'Ava', teamKey: 'frc254', matchKey: '2026mndu_qm12', tag: 'broke_down' });
    expect(liveFlagToast(flag!, 'Lead')).toEqual({ message: 'Ava: 254 · Broke down · QM 12', tone: 'warning' });
    expect(liveFlagToast({ ...flag!, tag: 'great_driver' }, 'Lead')?.tone).toBe('info');
  });

  it('never echoes a scout their own flag, and drops junk', () => {
    const flag = parseLiveFlag({ scout_profile: 'Ava', team_key: 'frc254', match_key: '2026mndu_qm12', tag: 'tipped' })!;
    expect(liveFlagToast(flag, ' ava ')).toBeNull();
    expect(parseLiveFlag({ scout_profile: 'Ava', team_key: 'frc254', match_key: 'x', tag: 'nope' })).toBeNull();
    expect(parseLiveFlag({ scout_profile: '', team_key: 'frc254', match_key: 'x', tag: 'tipped' })).toBeNull();
  });
});
