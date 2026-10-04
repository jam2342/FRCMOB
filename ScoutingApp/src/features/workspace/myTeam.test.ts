import { afterEach, describe, expect, it } from 'vitest';
import { signInTestWorkspace, signOutTestWorkspace } from '../../test/workspace';
import { MY_TEAM_STORAGE_KEY, readMyTeamKey, saveMyTeamKey } from './myTeam';

describe('my team', () => {
  afterEach(() => {
    signOutTestWorkspace();
    window.localStorage.removeItem(MY_TEAM_STORAGE_KEY);
  });

  it("uses the workspace's team number over anything this device saved", () => {
    signInTestWorkspace();
    window.localStorage.setItem(MY_TEAM_STORAGE_KEY, 'frc254');
    expect(readMyTeamKey()).toBe('frc118');
  });

  it('uses the device value for people without a workspace', () => {
    window.localStorage.setItem(MY_TEAM_STORAGE_KEY, '1678');
    expect(readMyTeamKey()).toBe('frc1678');
    window.localStorage.removeItem(MY_TEAM_STORAGE_KEY);
    expect(readMyTeamKey()).toBe('');
  });

  it('only saves a per-device choice when there is no workspace', () => {
    signInTestWorkspace();
    saveMyTeamKey('frc254');
    expect(window.localStorage.getItem(MY_TEAM_STORAGE_KEY)).toBeNull();
    signOutTestWorkspace();
    saveMyTeamKey('frc254');
    expect(window.localStorage.getItem(MY_TEAM_STORAGE_KEY)).toBe('frc254');
  });
});
