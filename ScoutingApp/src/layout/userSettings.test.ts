import { beforeEach, describe, expect, it } from 'vitest';
import {
  applyBodySettingsClasses,
  getStoredSettings,
  saveFavoriteEvents,
  saveFavoriteTeams,
  saveStoredSettings,
} from './userSettings';

describe('userSettings', () => {
  beforeEach(() => {
    window.localStorage.clear();
    document.body.className = '';
  });

  it('persists and reads settings', () => {
    saveStoredSettings({
      theme: 'light',
      density: 'compact',
      quickJumpMode: 'team',
      quickJumpRegion: 'tx',
      liveRefreshSec: 35,
    });
    const settings = getStoredSettings();
    expect(settings.theme).toBe('light');
    expect(settings.density).toBe('compact');
    expect(settings.quickJumpMode).toBe('team');
    expect(settings.quickJumpRegion).toBe('tx');
    expect(settings.liveRefreshSec).toBe(35);
  });

  // `Number(null)` is 0, which is finite — so an unset key used to clamp to the
  // 5s floor rather than fall through to the 60s default, and every fresh
  // browser polled twelve times harder than intended.
  it('falls back to the default refresh interval when nothing is stored', () => {
    expect(getStoredSettings().liveRefreshSec).toBe(60);
  });

  it('falls back to the default for a blank or unparseable refresh interval', () => {
    window.localStorage.setItem('scouting_live_refresh_sec', '   ');
    expect(getStoredSettings().liveRefreshSec).toBe(60);
    window.localStorage.setItem('scouting_live_refresh_sec', 'soon');
    expect(getStoredSettings().liveRefreshSec).toBe(60);
  });

  it('still clamps a stored interval into range', () => {
    window.localStorage.setItem('scouting_live_refresh_sec', '2');
    expect(getStoredSettings().liveRefreshSec).toBe(5);
    window.localStorage.setItem('scouting_live_refresh_sec', '999');
    expect(getStoredSettings().liveRefreshSec).toBe(120);
  });

  it('normalizes favorites and applies body classes', () => {
    const events = saveFavoriteEvents(['2026txhou', 'invalid', '2026txhou']);
    const teams = saveFavoriteTeams(['frc118', '118', 'bad']);
    expect(events).toEqual(['2026txhou']);
    expect(teams).toEqual(['frc118']);

    applyBodySettingsClasses({
      ...getStoredSettings(),
      theme: 'light',
      density: 'compact',
    });
    expect(document.body.classList.contains('theme-light')).toBe(true);
    expect(document.body.classList.contains('density-compact')).toBe(true);
  });
});
