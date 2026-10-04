// Keep failed draft writes within this tab so route changes still recover them.
// A null entry hides an old persisted draft whose removal failed.
const pendingDrafts = new Map<string, string | null>();

export function readRecoverableScoutDraft(key: string): string | null {
  if (pendingDrafts.has(key)) return pendingDrafts.get(key) ?? null;
  try { return localStorage.getItem(key); } catch { return null; }
}

export function persistRecoverableScoutDraft(key: string, value: string | null): boolean {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
    pendingDrafts.delete(key);
    return true;
  } catch {
    pendingDrafts.set(key, value);
    return false;
  }
}

if (typeof window !== 'undefined') {
  window.addEventListener('beforeunload', (event) => {
    if ([...pendingDrafts.values()].some((value) => value !== null)) event.preventDefault();
  });
}
