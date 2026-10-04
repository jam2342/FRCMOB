import { useCallback, useEffect, useRef, useState } from 'react';
import { QueuedForSyncError, updatePicklist, type Picklist } from '../../api';
import { getWorkspaceSession } from '../workspace/workspaceSession';

type Draft = { doc: Picklist; conflict?: Picklist; error?: string };
const memory = new Map<string, Draft>();
const keyFor = (workspaceId: number, id: number) => `frcmob_picklist_draft_v1:${workspaceId}:${id}`;

export function usePicklistEditor(workspaceId: number) {
  const [doc, setDoc] = useState<Picklist | null>(null);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState('');
  const [conflict, setConflict] = useState(false);
  const [pending, setPending] = useState(false);
  const confirmed = useRef(new Map<number, Picklist>());
  const active = useRef<Picklist | null>(null);
  const timers = useRef(new Map<number, ReturnType<typeof setTimeout>>());
  const flights = useRef(new Set<number>());
  const mounted = useRef(true);
  const saveRef = useRef<(id: number) => Promise<void>>(async () => {});

  const read = useCallback((id: number): Draft | null => {
    const key = keyFor(workspaceId, id);
    if (memory.has(key)) return memory.get(key)!;
    try {
      const draft = JSON.parse(localStorage.getItem(key) || 'null') as Draft | null;
      if (draft?.doc?.id === id && Array.isArray(draft.doc.slots)) {
        memory.set(key, draft);
        return draft;
      }
    } catch { /* Ignore an unreadable draft. */ }
    return null;
  }, [workspaceId]);

  const persist = useCallback((draft: Draft) => {
    const key = keyFor(workspaceId, draft.doc.id);
    memory.set(key, draft);
    try { localStorage.setItem(key, JSON.stringify(draft)); }
    catch {
      draft.error = 'Device storage is full. Keep this tab open until your changes are shared.';
    }
  }, [workspaceId]);

  const publish = useCallback((next: Picklist | null) => {
    active.current = next;
    if (!mounted.current) return;
    const draft = next ? read(next.id) : null;
    setDoc(next);
    setSaving(next ? flights.current.has(next.id) : false);
    setPending(Boolean(draft));
    setConflict(Boolean(draft?.conflict));
    setNotice(draft?.conflict
      ? 'Another scout changed this list. Your edits are kept on this device. Choose which version to share.'
      : draft?.error || (draft ? 'Changes kept on this device; waiting to share.' : ''));
  }, [read]);

  const save = useCallback(async (id: number) => {
    const draft = read(id);
    // Never start a delayed write under a different workspace's access.
    if (!draft || draft.conflict || flights.current.has(id) || !mounted.current || getWorkspaceSession()?.workspace.id !== workspaceId) return;
    const sent = draft.doc;
    flights.current.add(id);
    if (active.current?.id === id) publish(active.current);
    try {
      const result = await updatePicklist(id, {
        version: sent.version, slots: sent.slots, title: sent.title, live_mode: sent.live_mode,
      });
      confirmed.current.set(id, result.picklist);
      const latest = read(id);
      if (!latest) return;
      if (result.conflict) {
        persist({ ...latest, conflict: result.picklist });
      } else if (latest.doc === sent) {
        memory.delete(keyFor(workspaceId, id));
        try { localStorage.removeItem(keyFor(workspaceId, id)); } catch { /* Retained copies are safe to review on reopening. */ }
        if (active.current?.id === id) active.current = result.picklist;
      } else {
        // Acknowledge only the submitted snapshot; serialize the newer edits on its version.
        persist({ doc: { ...latest.doc, version: result.picklist.version } });
        if (active.current?.id === id) active.current = read(id)!.doc;
      }
    } catch (err) {
      const latest = read(id);
      if (latest) persist({ ...latest, error: err instanceof QueuedForSyncError
        ? 'Saved on this device for sync. Reopen this list after reconnecting to review the shared version.'
        : `${(err as Error).message || 'Could not share changes.'} Your edits are kept on this device. Retry when ready.` });
    } finally {
      flights.current.delete(id);
      if (active.current?.id === id) publish(read(id)?.doc ?? active.current);
    }
    const remaining = read(id);
    if (remaining && !remaining.conflict && !remaining.error && remaining.doc !== sent) await saveRef.current(id);
  }, [persist, publish, read, workspaceId]);

  useEffect(() => { saveRef.current = save; }, [save]);
  useEffect(() => {
    mounted.current = true;
    const timerMap = timers.current;
    const warn = (event: BeforeUnloadEvent) => {
      if ([...memory.keys()].some(key => key.startsWith(`frcmob_picklist_draft_v1:${workspaceId}:`))) event.preventDefault();
    };
    window.addEventListener('beforeunload', warn);
    return () => {
      mounted.current = false;
      for (const timer of timerMap.values()) clearTimeout(timer);
      timerMap.clear();
      window.removeEventListener('beforeunload', warn);
    };
  }, [workspaceId]);

  const select = useCallback((server: Picklist | null) => {
    if (!server) { publish(null); return; }
    const known = confirmed.current.get(server.id);
    if (known && known.version > server.version) server = known;
    const draft = read(server.id);
    if (draft && draft.doc.event_key === server.event_key) {
      if (draft.doc.version < server.version) persist({ ...draft, conflict: server });
      publish(read(server.id)!.doc);
    } else publish(server);
  }, [persist, publish, read]);

  const edit = useCallback((update: (current: Picklist) => Picklist) => {
    const current = active.current;
    if (!current) return;
    const previous = read(current.id);
    const next = update(current);
    persist({ doc: next, conflict: previous?.conflict });
    publish(next);
    const timer = timers.current.get(next.id);
    if (timer) clearTimeout(timer);
    timers.current.set(next.id, setTimeout(() => {
      timers.current.delete(next.id);
      void saveRef.current(next.id);
    }, 900));
  }, [persist, publish, read]);

  const retry = useCallback(() => {
    const current = active.current;
    if (!current) return;
    const draft = read(current.id);
    if (!draft) return;
    persist({ doc: { ...draft.doc, version: draft.conflict?.version ?? draft.doc.version } });
    publish(read(current.id)!.doc);
    void saveRef.current(current.id);
  }, [persist, publish, read]);

  const discard = useCallback(() => {
    const current = active.current;
    if (!current) return;
    const draft = read(current.id);
    const shared = draft?.conflict;
    if (!shared) return;
    memory.delete(keyFor(workspaceId, current.id));
    try { localStorage.removeItem(keyFor(workspaceId, current.id)); } catch { /* Ignore unavailable storage. */ }
    publish(shared);
  }, [publish, read, workspaceId]);

  const pause = useCallback(() => {
    const id = active.current?.id;
    if (id == null) return;
    const timer = timers.current.get(id);
    if (timer) clearTimeout(timer);
    timers.current.delete(id);
  }, []);

  const remove = useCallback((id: number) => {
    const timer = timers.current.get(id);
    if (timer) clearTimeout(timer);
    timers.current.delete(id);
    memory.delete(keyFor(workspaceId, id));
    try { localStorage.removeItem(keyFor(workspaceId, id)); } catch { /* Ignore unavailable storage. */ }
    if (active.current?.id === id) publish(null);
  }, [publish, workspaceId]);

  return { doc, saving, pending, notice, conflict, select, edit, retry, discard, remove, pause };
}
