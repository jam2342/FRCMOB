import { useEffect, useState } from 'react';
import { listPitEntries, type PitScoutingEntry } from '../../api';
import { useWorkspace } from '../workspace/useWorkspace';

// Team Center, the peek, Compare and the picklist all ask for the same event's pit notes. One
// request per workspace + event is shared between them; it is reused for a short while so
// moving between those screens is instant, and short enough that a fresh pit save shows up.
const FRESH_MS = 30_000;

type Entries = Map<string, PitScoutingEntry>;
type Slot = { promise: Promise<Entries>; at: number; value: Entries | null };
const slots = new Map<string, Slot>();

function slotKey(workspaceId: number, eventKey: string): string {
  return `${workspaceId}:${eventKey}`;
}

export function loadPitEntries(workspaceId: number, eventKey: string): Promise<Entries> {
  const key = slotKey(workspaceId, eventKey);
  const existing = slots.get(key);
  if (existing && Date.now() - existing.at < FRESH_MS) return existing.promise;
  const slot: Slot = { at: Date.now(), value: existing?.value ?? null, promise: Promise.resolve(new Map()) };
  slot.promise = listPitEntries(eventKey)
    .then((result) => {
      const map: Entries = new Map((result.entries ?? []).map((entry) => [entry.team_key.toLowerCase(), entry] as const));
      slot.value = map;
      return map;
    })
    .catch((error) => {
      // A failure is not kept: the next screen asks again.
      if (slots.get(key) === slot) slots.delete(key);
      throw error;
    });
  slots.set(key, slot);
  return slot.promise;
}

export function resetPitEntriesCache(): void {
  slots.clear();
}

export interface PitEntriesState {
  // False outside a workspace: pit notes are private to one, so callers render nothing.
  inWorkspace: boolean;
  loading: boolean;
  failed: boolean;
  entries: Entries;
}

const NONE: Entries = new Map();

export function usePitEntries(eventKey: string | null | undefined): PitEntriesState {
  const workspace = useWorkspace();
  const workspaceId = workspace?.workspace.id ?? null;
  const event = (eventKey || '').trim().toLowerCase();
  const key = workspaceId !== null && event ? slotKey(workspaceId, event) : '';
  const [loaded, setLoaded] = useState<{ key: string; entries: Entries | null } | null>(null);

  useEffect(() => {
    if (workspaceId === null || !key) return undefined;
    let cancelled = false;
    loadPitEntries(workspaceId, event)
      .then((entries) => {
        if (!cancelled) setLoaded({ key, entries });
      })
      .catch(() => {
        if (!cancelled) setLoaded({ key, entries: null });
      });
    return () => {
      cancelled = true;
    };
  }, [workspaceId, event, key]);

  const current = loaded && loaded.key === key ? loaded : null;
  const stale = key ? slots.get(key)?.value ?? null : null;
  return {
    inWorkspace: workspaceId !== null,
    loading: Boolean(key) && !current && !stale,
    failed: Boolean(key) && current !== null && current.entries === null && !stale,
    entries: current?.entries ?? stale ?? NONE,
  };
}
