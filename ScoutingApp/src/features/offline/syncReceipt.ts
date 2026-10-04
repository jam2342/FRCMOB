const KEY = 'frcmob_last_confirmed_sync_v1';
const ISSUE_KEY = 'frcmob_sync_issue_v1';
export type SyncIssue = { reason: string; count: number; labels: string[]; at: number };

export function recordConfirmedSync(): void {
  try { window.localStorage.setItem(KEY, String(Date.now())); } catch { /* receipt is optional */ }
  window.dispatchEvent(new Event('frcmob:sync-receipt'));
}

export function lastConfirmedSync(): number | null {
  try {
    const value = Number(window.localStorage.getItem(KEY));
    return Number.isFinite(value) && value > 0 ? value : null;
  } catch { return null; }
}

export function recordSyncIssue(issue: Omit<SyncIssue, 'at'>): void {
  try { window.localStorage.setItem(ISSUE_KEY, JSON.stringify({ ...issue, at: Date.now() })); } catch { /* UI banner still appears */ }
  window.dispatchEvent(new Event('frcmob:sync-receipt'));
}

export function lastSyncIssue(): SyncIssue | null {
  try {
    const issue = JSON.parse(window.localStorage.getItem(ISSUE_KEY) || 'null') as SyncIssue | null;
    return issue && typeof issue.count === 'number' && Array.isArray(issue.labels) ? issue : null;
  } catch { return null; }
}

export function clearSyncIssue(): void {
  try { window.localStorage.removeItem(ISSUE_KEY); } catch { /* ignore */ }
  window.dispatchEvent(new Event('frcmob:sync-receipt'));
}
