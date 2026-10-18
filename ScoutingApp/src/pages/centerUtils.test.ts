import { describe, expect, it } from 'vitest';
import {
  buildMatchCenterPath,
  eventKeyFromMatchKey,
  isTransientAbortLikeError,
  liveTimerLabel,
  normalizeMatchKey,
  normalizeTeamKeyInput,
} from './centerUtils';

describe('centerUtils', () => {
  it('normalizes team input consistently', () => {
    expect(normalizeTeamKeyInput('118')).toBe('frc118');
    expect(normalizeTeamKeyInput('frc254')).toBe('frc254');
    expect(normalizeTeamKeyInput('bad-key')).toBeNull();
  });

  it('classifies timer state correctly', () => {
    const nowMs = 1_000_000 * 1000;
    expect(liveTimerLabel(1_000_120, nowMs).state).toBe('upcoming');
    expect(liveTimerLabel(999_950, nowMs).state).toBe('live');
    expect(liveTimerLabel(999_800, nowMs).state).toBe('ended');
  });

  it('classifies abort-like transient request errors', () => {
    expect(isTransientAbortLikeError(new Error('The user aborted a request.'))).toBe(true);
    expect(isTransientAbortLikeError('Request timed out after 12s')).toBe(true);
    expect(isTransientAbortLikeError('Validation failed for team key')).toBe(false);
  });

  it('normalizes match keys and extracts event key', () => {
    expect(normalizeMatchKey('qm 25', '2026txda')).toBe('2026txda_qm25');
    expect(normalizeMatchKey('2026txda_qm25', '2026txda')).toBe('2026txda_qm25');
    expect(eventKeyFromMatchKey('2026txda_qm25')).toBe('2026txda');
    expect(eventKeyFromMatchKey('qm25')).toBeNull();
  });

  it('builds match center deep-link paths from event and match keys', () => {
    expect(buildMatchCenterPath('2026txda', 'qm 25')).toBe('/match-center?event=2026txda&match=2026txda_qm25');
    expect(buildMatchCenterPath('2026txda', '2026txda_qm25')).toBe('/match-center?event=2026txda&match=2026txda_qm25');
    expect(buildMatchCenterPath('2026txda', '2026cada_qm25')).toBe('/match-center?event=2026cada&match=2026cada_qm25');
    expect(buildMatchCenterPath('', '')).toBe('/match-center');
  });
});

describe('countdowns far from the start', () => {
  it('reads hours and days instead of thousands of minutes', () => {
    const now = 1_000_000_000;
    expect(liveTimerLabel(1_000_000 + 600, now).value).toBe('10:00');
    expect(liveTimerLabel(1_000_000 + 18 * 3600 + 54 * 60, now).value).toBe('18h 54m');
    expect(liveTimerLabel(1_000_000 + 2 * 86400 + 3 * 3600, now).value).toBe('2d 3h');
  });
});

