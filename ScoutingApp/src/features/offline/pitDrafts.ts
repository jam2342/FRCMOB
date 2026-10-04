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
export function savePitDraft(workspaceId: number, event: string, team: string, payload: Record<string,unknown>): boolean {
  const id = key(workspaceId,event,team);
  try { localStorage.setItem(id,JSON.stringify({payload,savedAt:Date.now()})); volatileDrafts.delete(id); return true; } catch { volatileDrafts.set(id,{...payload}); return false; }
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
