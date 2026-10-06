import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ensureTeamRoom,
  getEventSchedule,
  getTeamRoom,
  saveTeamRoomAssignments,
  type EventScheduleItem,
  type TeamRoomAssignmentChange,
  type TeamRoomSnapshot,
} from '../../api';
import { useOnlineStatus } from '../../hooks/useOnlineStatus';
import { getOrCreateScoutingRoomClientId } from '../../pages/scoutingRoomClientId';
import { useWorkspace } from './useWorkspace';

const POLL_MS = 5_000;
const SCHEDULE_POLL_MS = 60_000;
const SNAPSHOT_STORAGE_PREFIX = 'team_room_snapshot_v1';
const TEAM_ROOM_KEYS_STORAGE = 'team_room_keys_v1';

export type TeamRoomStatus = 'idle' | 'loading' | 'ready' | 'error';

export type SaveResult = { ok: true } | { ok: false; error: string };

// Keyed by member too: "me", the role and covered_by_me belong to one person,
// and a team can share a phone.
function snapshotStorageKey(workspaceId: number, memberId: number, eventKey: string): string {
  return `${SNAPSHOT_STORAGE_PREFIX}:${workspaceId}:${memberId}:${eventKey}`;
}

// The last snapshot lets a scout at a venue with no signal still see their list.
function readSavedSnapshot(workspaceId: number, memberId: number, eventKey: string): TeamRoomSnapshot | null {
  try {
    const raw = localStorage.getItem(snapshotStorageKey(workspaceId, memberId, eventKey));
    const parsed = raw ? (JSON.parse(raw) as TeamRoomSnapshot) : null;
    return parsed && parsed.event_key === eventKey && parsed.me?.member_id === memberId && Array.isArray(parsed.assignments)
      ? parsed
      : null;
  } catch {
    return null;
  }
}

function saveSnapshot(workspaceId: number, memberId: number, snapshot: TeamRoomSnapshot): void {
  try {
    localStorage.setItem(snapshotStorageKey(workspaceId, memberId, snapshot.event_key), JSON.stringify(snapshot));
  } catch {
    // Storage full or blocked: the live copy still works.
  }
}

// Room keys this device knows are team rooms, so Scouting can move between
// them on an event switch without pulling anyone out of a room they chose.
export function isTeamRoomKey(roomKey: string): boolean {
  try {
    const keys = JSON.parse(localStorage.getItem(TEAM_ROOM_KEYS_STORAGE) || '[]') as string[];
    return keys.includes(roomKey.toLowerCase());
  } catch {
    return false;
  }
}

function rememberTeamRoomKey(roomKey: string): void {
  try {
    const keys = JSON.parse(localStorage.getItem(TEAM_ROOM_KEYS_STORAGE) || '[]') as string[];
    const key = roomKey.toLowerCase();
    if (keys.includes(key)) return;
    localStorage.setItem(TEAM_ROOM_KEYS_STORAGE, JSON.stringify([...keys, key].slice(-50)));
  } catch {
    // Without the list, Scouting just won't auto-switch between team rooms.
  }
}

function errorText(error: unknown): string {
  return error instanceof Error && error.message ? error.message : 'Something went wrong. Try again.';
}

export function useTeamRoom(eventKey: string | null | undefined) {
  const session = useWorkspace();
  const { online } = useOnlineStatus();
  const workspaceId = session?.workspace.id ?? null;
  const memberId = session?.me.id ?? null;
  const event = String(eventKey || '').trim().toLowerCase();
  const [snapshot, setSnapshot] = useState<TeamRoomSnapshot | null>(null);
  const [schedule, setSchedule] = useState<EventScheduleItem[] | null>(null);
  const [eventName, setEventName] = useState('');
  const [status, setStatus] = useState<TeamRoomStatus>('idle');
  const [error, setError] = useState('');
  const [fromSavedCopy, setFromSavedCopy] = useState(false);
  // Responses for an event, workspace or person the page has already left must not land.
  const scopeRef = useRef('');
  const scope = workspaceId && memberId && event ? `${workspaceId}:${memberId}:${event}` : '';
  // Every request takes a number when it starts. A poll that set off before a
  // save must not land after it and put the old assignments back.
  const requestSeqRef = useRef(0);
  const appliedSeqRef = useRef(0);
  // A poll that starts mid-save could read the room before the save commits.
  const savesInFlightRef = useRef(0);
  // Saves run one at a time, so the order they start in is the order the server
  // commits them, and the last response really is the newest state.
  const saveChainRef = useRef<Promise<unknown>>(Promise.resolve());
  const refetchRef = useRef<(() => Promise<void>) | null>(null);
  // Polls usually return exactly what we have. Setting it again re-rendered the
  // whole Scouting page every 5 s on a phone, so unchanged answers are dropped.
  const snapshotJsonRef = useRef('');
  const scheduleJsonRef = useRef('');

  const nextSeq = useCallback(() => {
    requestSeqRef.current += 1;
    return requestSeqRef.current;
  }, []);

  const accept = useCallback((response: TeamRoomSnapshot, forScope: string, seq: number) => {
    if (scopeRef.current !== forScope || !workspaceId || !memberId) return;
    if (response.me?.member_id !== memberId || seq < appliedSeqRef.current) return;
    // Only the snapshot itself: the create response also carries `created` and
    // the room token, which has its own storage and has no place in the saved copy.
    const next: TeamRoomSnapshot = {
      ok: response.ok,
      room_key: response.room_key,
      event_key: response.event_key,
      me: response.me,
      members: response.members,
      assignments: response.assignments,
    };
    appliedSeqRef.current = seq;
    setFromSavedCopy(false);
    setStatus('ready');
    setError('');
    const json = JSON.stringify(next);
    if (json === snapshotJsonRef.current) return;
    snapshotJsonRef.current = json;
    setSnapshot(next);
    saveSnapshot(workspaceId, memberId, next);
    rememberTeamRoomKey(next.room_key);
  }, [memberId, workspaceId]);

  useEffect(() => {
    scopeRef.current = scope;
    appliedSeqRef.current = requestSeqRef.current;
    setError('');
    if (!scope || !workspaceId || !memberId) {
      setSnapshot(null);
      setSchedule(null);
      setEventName('');
      setStatus('idle');
      return;
    }
    const saved = readSavedSnapshot(workspaceId, memberId, event);
    snapshotJsonRef.current = saved ? JSON.stringify(saved) : '';
    scheduleJsonRef.current = '';
    setSnapshot(saved);
    setFromSavedCopy(Boolean(saved));
    setSchedule(null);
    setEventName('');
    setStatus('loading');

    let cancelled = false;
    let scheduleInFlight = false;
    let pollInFlight = false;
    let refreshQueued = false;
    const loadSchedule = async () => {
      if (cancelled || document.visibilityState !== 'visible' || scheduleInFlight) return;
      scheduleInFlight = true;
      try {
        const result = await getEventSchedule(event);
        if (cancelled || scopeRef.current !== scope) return;
        setEventName(result.event_name || event);
        const json = JSON.stringify(result.matches);
        if (json === scheduleJsonRef.current) return;
        scheduleJsonRef.current = json;
        setSchedule(result.matches);
      } catch (err) {
        if (cancelled || scopeRef.current !== scope) return;
        setSchedule((current) => current ?? []);
        setError((current) => current || errorText(err));
      } finally {
        scheduleInFlight = false;
      }
    };
    // Until the room opens, the 5 s poll retries it on a growing delay instead
    // of firing a create request every tick at an event that keeps refusing.
    let roomOpen = false;
    let failures = 0;
    let nextOpenAt = 0;
    const open = async () => {
      const seq = nextSeq();
      try {
        accept(await ensureTeamRoom(event, getOrCreateScoutingRoomClientId()), scope, seq);
        roomOpen = true;
        failures = 0;
      } catch (err) {
        if (cancelled || scopeRef.current !== scope) return;
        failures += 1;
        nextOpenAt = Date.now() + Math.min(60_000, POLL_MS * 2 ** (failures - 1));
        setStatus('error');
        setError(errorText(err));
      }
    };
    const poll = async () => {
      if (cancelled || document.visibilityState !== 'visible' || savesInFlightRef.current > 0 || pollInFlight) return;
      pollInFlight = true;
      try {
        if (!roomOpen) {
          if (Date.now() >= nextOpenAt) await open();
          return;
        }
        const seq = nextSeq();
        try {
          const next = await getTeamRoom(event);
          if (cancelled) return;
          if (next) accept(next, scope, seq);
          else {
            roomOpen = false;
            await open();
          }
        } catch {
          // Keep showing the last good copy; the next poll or focus retries.
        }
      } finally {
        pollInFlight = false;
        if (refreshQueued && !cancelled) {
          refreshQueued = false;
          void poll();
        }
      }
    };
    const refetch = async () => {
      if (pollInFlight) { refreshQueued = true; return; }
      await poll();
    };
    refetchRef.current = refetch;
    let timer: number | undefined;
    let scheduleTimer: number | undefined;
    const stopTimers = () => {
      window.clearInterval(timer);
      window.clearInterval(scheduleTimer);
      timer = scheduleTimer = undefined;
    };
    const startTimers = () => {
      if (timer !== undefined) return;
      timer = window.setInterval(() => { void poll(); }, POLL_MS);
      scheduleTimer = window.setInterval(() => { void loadSchedule(); }, SCHEDULE_POLL_MS);
    };
    if (document.visibilityState === 'visible') {
      void loadSchedule();
      void poll();
      startTimers();
    }
    const onVisible = () => {
      if (document.visibilityState !== 'visible') { stopTimers(); return; }
      startTimers();
      nextOpenAt = 0;
      void poll();
      void loadSchedule();
    };
    document.addEventListener('visibilitychange', onVisible);
    window.addEventListener('focus', onVisible);
    return () => {
      cancelled = true;
      stopTimers();
      document.removeEventListener('visibilitychange', onVisible);
      window.removeEventListener('focus', onVisible);
      if (refetchRef.current === refetch) refetchRef.current = null;
    };
  }, [accept, event, memberId, nextSeq, scope, workspaceId]);

  const saveChanges = useCallback(async (changes: TeamRoomAssignmentChange[]): Promise<SaveResult> => {
    if (!changes.length) return { ok: true };
    if (!event || !scope) return { ok: false, error: 'Choose an event first.' };
    if (!online) return { ok: false, error: "You're offline. Assignments can be changed once you're back online." };
    savesInFlightRef.current += 1;
    const attempt = saveChainRef.current.then(async (): Promise<SaveResult> => {
      const seq = nextSeq();
      try {
        accept(await saveTeamRoomAssignments(event, changes), scope, seq);
        return { ok: true };
      } catch (err) {
        return { ok: false, error: errorText(err) };
      }
    });
    saveChainRef.current = attempt;
    const result = await attempt;
    savesInFlightRef.current -= 1;
    // Polls paused during the saves; catch up with anything another leader did.
    if (savesInFlightRef.current === 0) void refetchRef.current?.();
    return result;
  }, [accept, event, nextSeq, online, scope]);

  const isLeader = (snapshot?.me.role ?? session?.me.role) === 'leader';

  return {
    workspace: session,
    eventKey: event,
    eventName,
    snapshot,
    schedule,
    status,
    error,
    online,
    fromSavedCopy,
    isLeader,
    saveChanges,
  } as const;
}
