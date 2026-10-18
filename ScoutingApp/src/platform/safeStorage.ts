// localStorage that can't take the app down.
//
// Reading window.localStorage throws when the browser blocks site data ("Block all
// cookies", some embedded and managed browsers), and setItem throws when the origin's
// ~5 MB is full. Either one, unguarded in a shell setting, left a blank page.
//
// installStorageFallback() runs first thing at startup: if storage can't be reached at
// all, the app gets an in-memory stand-in for this visit (it works, it just forgets).
// The helpers below are for preferences and other writes that may quietly fail.
// Saving scouting work does NOT use them: it must say when the phone couldn't keep it.

class MemoryStorage implements Storage {
  private data = new Map<string, string>();
  get length() { return this.data.size; }
  clear() { this.data.clear(); }
  getItem(key: string) { return this.data.has(String(key)) ? this.data.get(String(key))! : null; }
  key(index: number) { return [...this.data.keys()][index] ?? null; }
  removeItem(key: string) { this.data.delete(String(key)); }
  setItem(key: string, value: string) { this.data.set(String(key), String(value)); }
}

let fallbackInstalled = false;

export function installStorageFallback(): boolean {
  if (typeof window === 'undefined' || fallbackInstalled) return fallbackInstalled;
  for (const name of ['localStorage', 'sessionStorage'] as const) {
    let reachable = true;
    try {
      const storage = window[name];
      if (!storage) reachable = false;
      else storage.getItem('__frcmob_probe__');
    } catch {
      reachable = false;
    }
    if (reachable) continue;
    try {
      Object.defineProperty(window, name, { configurable: true, value: new MemoryStorage() });
      fallbackInstalled = true;
    } catch {
      // Can't replace it either; the guarded helpers still keep the app up.
    }
  }
  return fallbackInstalled;
}

export function storageGet(key: string): string | null {
  try { return window.localStorage.getItem(key); } catch { return null; }
}

export function storageSet(key: string, value: string): boolean {
  try { window.localStorage.setItem(key, value); return true; } catch { return false; }
}

export function storageRemove(key: string): void {
  try { window.localStorage.removeItem(key); } catch { /* Nothing to keep consistent. */ }
}
