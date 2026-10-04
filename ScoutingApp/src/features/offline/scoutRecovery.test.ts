import { afterEach, describe, expect, it, vi } from 'vitest';
import { storageMethodTarget } from '../../test/storage';
import { persistRecoverableScoutDraft, readRecoverableScoutDraft } from './scoutRecovery';

afterEach(() => {
  vi.restoreAllMocks();
  for (const key of ['scout-a', 'cleared-scout']) persistRecoverableScoutDraft(key, null);
  localStorage.clear();
});

describe('scout recovery when device storage fails', () => {
  it('recovers the latest draft in this tab and keeps workspaces separate', () => {
    localStorage.setItem('scout-a', 'old draft');
    const write = vi.spyOn(storageMethodTarget(), 'setItem').mockImplementation(() => {
      throw new DOMException('full', 'QuotaExceededError');
    });
    expect(persistRecoverableScoutDraft('scout-a', 'latest draft')).toBe(false);
    expect(readRecoverableScoutDraft('scout-a')).toBe('latest draft');
    expect(readRecoverableScoutDraft('scout-b')).toBeNull();
    const unload = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(unload);
    expect(unload.defaultPrevented).toBe(true);
    write.mockRestore();
    expect(persistRecoverableScoutDraft('scout-a', 'latest draft')).toBe(true);
    expect(localStorage.getItem('scout-a')).toBe('latest draft');
    persistRecoverableScoutDraft('scout-a', null);
  });

  it('does not resurrect an old draft when its removal fails', () => {
    localStorage.setItem('cleared-scout', 'old draft');
    const remove = vi.spyOn(storageMethodTarget(), 'removeItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    expect(persistRecoverableScoutDraft('cleared-scout', null)).toBe(false);
    expect(readRecoverableScoutDraft('cleared-scout')).toBeNull();
    const unload = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(unload);
    expect(unload.defaultPrevented).toBe(false);
    remove.mockRestore();
    persistRecoverableScoutDraft('cleared-scout', null);
  });
});
