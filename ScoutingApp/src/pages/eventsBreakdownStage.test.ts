import { describe, expect, it } from 'vitest';
import { resolveBreakdownStage } from './eventsPage.helpers';

describe('resolveBreakdownStage', () => {
  it('keeps a user-selected stage while user lock is enabled', () => {
    expect(resolveBreakdownStage('knockout', true, false, true)).toBe('knockout');
    expect(resolveBreakdownStage('qualifying', false, true, true)).toBe('qualifying');
  });

  it('prefers qualifying when both stages are available', () => {
    expect(resolveBreakdownStage('qualifying', true, true, false)).toBe('qualifying');
    expect(resolveBreakdownStage('knockout', true, true, false)).toBe('knockout');
  });

  it('falls back to the stage that has data when not locked', () => {
    expect(resolveBreakdownStage('knockout', true, false, false)).toBe('qualifying');
    expect(resolveBreakdownStage('qualifying', false, true, false)).toBe('knockout');
  });
});
