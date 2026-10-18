import { afterEach, describe, expect, it, vi } from 'vitest';
import { API_CACHE_PREFIX, apiCacheChars, freeApiCacheSpace, setItemMakingRoom } from './apiCacheSpace';

const cached = (name: string, storedAtMs: number, size = 100) =>
  localStorage.setItem(`${API_CACHE_PREFIX}${name}`, JSON.stringify({ storedAtMs, bodyText: 'x'.repeat(size) }));

describe('saved API copies give way to scouting work', () => {
  afterEach(() => { vi.restoreAllMocks(); localStorage.clear(); });

  it('frees the oldest copies first, down to the target', () => {
    cached('old', 1);
    cached('mid', 2);
    cached('new', 3);
    localStorage.setItem('scouting_manual_entries_v2:1', '[]');
    const each = apiCacheChars() / 3;
    freeApiCacheSpace(each * 2);
    expect(localStorage.getItem(`${API_CACHE_PREFIX}old`)).toBeNull();
    expect(localStorage.getItem(`${API_CACHE_PREFIX}new`)).not.toBeNull();
    freeApiCacheSpace(0);
    expect(apiCacheChars()).toBe(0);
    expect(localStorage.getItem('scouting_manual_entries_v2:1')).toBe('[]');
  });

  it('a save that hits a full disk clears the copies and is retried once', () => {
    cached('page', 1);
    const real = Storage.prototype.setItem;
    let calls = 0;
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(function (this: Storage, key: string, value: string) {
      calls += 1;
      if (calls === 1) throw new DOMException('full', 'QuotaExceededError');
      return real.call(this, key, value);
    });
    setItemMakingRoom('scouting_manual_entries_v2:1', '[{"id":"a"}]');
    expect(localStorage.getItem('scouting_manual_entries_v2:1')).toBe('[{"id":"a"}]');
    expect(apiCacheChars()).toBe(0);
  });

  it('still reports a full disk when there was nothing of its own to free', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new DOMException('full', 'QuotaExceededError'); });
    expect(() => setItemMakingRoom('scouting_manual_entries_v2:1', '[]')).toThrow();
  });
});
