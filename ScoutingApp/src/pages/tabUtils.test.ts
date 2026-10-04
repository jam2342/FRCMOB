import { describe, expect, it } from 'vitest';
import { resolveTab } from './tabUtils';

describe('resolveTab', () => {
  const tabs = ['overview', 'schedule', 'teams'] as const;

  it('keeps a handled tab value', () => {
    expect(resolveTab('schedule', tabs, 'overview')).toBe('schedule');
  });

  it.each([null, '', 'alliance', 'unknown'])('falls back for invalid tab value %s', (value) => {
    expect(resolveTab(value, tabs, 'overview')).toBe('overview');
  });

  it('falls back when a known tab is unavailable to the current user', () => {
    expect(resolveTab('advanced', ['overview', 'performance'] as const, 'overview')).toBe('overview');
  });
});
