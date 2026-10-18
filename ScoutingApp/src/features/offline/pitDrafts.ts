import { setItemMakingRoom } from '../../utils/apiCacheSpace';
// Private pit drafts stay on this device and never cross workspace/event/robot keys.
const volatileDrafts = new Map<string,Record<string,unknown>>();
if (typeof window !== 'undefined') window.addEventListener('beforeunload', event => {if(volatileDrafts.size) event.preventDefault();});
const key = (workspaceId: number, event: string, team: string) => `frcmob_pit_draft_v1:${workspaceId}:${encodeURIComponent(event)}:${encodeURIComponent(team)}`;
export function readPitDraft(workspaceId: number, event: string, team: string): Record<string, unknown> | null {
  const volatile = volatileDrafts.get(key(workspaceId,event,team));
  if (volatile) return {...volatile};
  try {
    const row = JSON.parse(localStorage.getItem(key(workspaceId,event,team)) || 'null');
    return row?.payload && typeof row.payload === 'object' && !Array.isArray(row.payload) ? row.payload : null;
  } catch { return null; }
}
// When the draft was last saved on this phone, so a page can tell that a teammate's
// server copy is newer.
export function readPitDraftSavedAt(workspaceId: number, event: string, team: string): number | null {
  try {
    const row = JSON.parse(localStorage.getItem(key(workspaceId,event,team)) || 'null');
    return typeof row?.savedAt === 'number' ? row.savedAt : null;
  } catch { return null; }
}
export function savePitDraft(workspaceId: number, event: string, team: string, payload: Record<string,unknown>): boolean {
  const id = key(workspaceId,event,team);
  try { setItemMakingRoom(id,JSON.stringify({payload,savedAt:Date.now()})); volatileDrafts.delete(id); return true; } catch { volatileDrafts.set(id,{...payload}); return false; }
}
export function clearConfirmedPitDraft(workspaceId: number, event: string, team: string, confirmed: Record<string,unknown>): void {
  try { if (JSON.stringify(readPitDraft(workspaceId,event,team)) === JSON.stringify(confirmed)) {localStorage.removeItem(key(workspaceId,event,team));volatileDrafts.delete(key(workspaceId,event,team));} } catch { /* Keep the draft if removal fails. */ }
}

const selectionKey = (workspaceId: number, event: string) => `frcmob_pit_selection_v1:${workspaceId}:${encodeURIComponent(event)}`;
export function readPitSelection(workspaceId: number, event: string): string | null {
  try { const team = localStorage.getItem(selectionKey(workspaceId,event)); return team && /^frc\d{1,5}$/.test(team) ? team : null; } catch {return null;}
}
export function rememberPitSelection(workspaceId: number, event: string, team: string): void {
  try {localStorage.setItem(selectionKey(workspaceId,event),team);} catch { /* Draft recovery still works by selecting the team. */ }
}

export function pitDraftPersisted(workspaceId: number, event: string, team: string): boolean {
  return !volatileDrafts.has(key(workspaceId,event,team)) && Boolean(readPitDraft(workspaceId,event,team));
}

// A pit save queued offline can be delivered while the Pit page is closed. The page's
// own watcher never sees that, so the old draft stayed and later hid newer notes a
// teammate saved. Settle it here instead: a draft identical to what was delivered is done.
let draftCleanupStarted = false;
export function startPitDraftCleanup(currentWorkspaceId: () => number | null): void {
  if (draftCleanupStarted || typeof window === 'undefined') return;
  draftCleanupStarted = true;
  window.addEventListener('offlinequeue:delivered', (event) => {
    const detail = (event as CustomEvent<{ url?: string; method?: string; body?: string | null }>).detail;
    if (!detail?.url || detail.method !== 'POST' || !/\/pit-scouting\/?$/.test(detail.url.split('?')[0])) return;
    const workspaceId = currentWorkspaceId();
    if (workspaceId === null) return;
    try {
      const body = JSON.parse(detail.body || '{}') as { event_key?: string; team_key?: string; payload?: Record<string, unknown> };
      if (body.event_key && body.team_key && body.payload) clearConfirmedPitDraft(workspaceId, body.event_key, body.team_key, body.payload);
    } catch { /* Not a pit save body; nothing to settle. */ }
  });
}
