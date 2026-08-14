import { describe, expect, it } from 'vitest';
import {
  buildMatchCenterPath,
  eventKeyFromMatchKey,
  isTransientAbortLikeError,
  liveTimerLabel,
  normalizeMatchKey,
  normalizeTeamKeyInput,
  summarizeFreshness,
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

  it('summarizes freshness payloads with stale warnings', () => {
    const stale = summarizeFreshness({
      is_outdated: true,
      latest_match_age_days: 6.4,
      warnings: ['Temporarily using fallback season context'],
    });
    expect(stale.state).toBe('stale');
    expect(stale.label).toContain('Stale');
    expect(stale.detail).toContain('fallback season');

    const fresh = summarizeFreshness({
      is_outdated: false,
      latest_match_age_days: 0.6,
      warnings: [],
    });
    expect(fresh.state).toBe('fresh');
    expect(fresh.label).toContain('Fresh');
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
