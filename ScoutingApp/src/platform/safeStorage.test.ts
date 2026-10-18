import { afterEach, describe, expect, it, vi } from 'vitest';

describe('safe storage', () => {
  afterEach(() => { vi.restoreAllMocks(); vi.resetModules(); });

  it('swaps in memory storage when the browser blocks site data', async () => {
    const original = Object.getOwnPropertyDescriptor(window, 'localStorage');
    Object.defineProperty(window, 'localStorage', { configurable: true, get() { throw new DOMException('blocked', 'SecurityError'); } });
    try {
      const { installStorageFallback, storageGet, storageSet } = await import('./safeStorage');
      expect(installStorageFallback()).toBe(true);
      expect(storageSet('k', 'v')).toBe(true);
      expect(storageGet('k')).toBe('v');
      expect(window.localStorage.getItem('k')).toBe('v');
    } finally {
      if (original) Object.defineProperty(window, 'localStorage', original);
    }
  });

  it('leaves working storage alone and never throws when it is full', async () => {
    const { installStorageFallback, storageGet, storageSet, storageRemove } = await import('./safeStorage');
    expect(installStorageFallback()).toBe(false);
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new DOMException('full', 'QuotaExceededError'); });
    expect(storageSet('k', 'v')).toBe(false);
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('gone'); });
    expect(storageGet('k')).toBeNull();
    expect(() => storageRemove('k')).not.toThrow();
  });
});
