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
