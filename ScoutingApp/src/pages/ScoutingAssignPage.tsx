import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  createOrJoinScoutingRoom,
  getEventSchedule,
  getScoutingRoomAssignments,
  getStoredRoomAccessToken,
  replaceScoutingRoomAssignments,
  setStoredRoomAccessToken,
  upsertScoutingRoomAssignment,
} from '../api';
import type {
  EventScheduleItem,
  ScoutingRoomAssignmentRecord,
  ScoutingRoomPresenceMember,
} from '../api';
import { EventPicker } from '../components/EventPicker';
import { PageViewBar } from '../components/PageViewBar';
import { SCOUTING_VIEWS } from '../components/pageViewBarConfig';
import { SurfaceCard, SurfaceCardGroup } from '../components/ui/SurfaceCard';
import { useEventKeyParam } from '../hooks/useEventKeyParam';
import { useMobileLayout } from '../hooks/useMobileLayout';
import { getOrCreateScoutingRoomClientId } from './scoutingRoomClientId';

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

const STORAGE_KEY_EVENT = 'scouting_center_event_key';
const STORAGE_KEY_SCOUTS = 'scouting_assign_scouts_v1';
const STORAGE_KEY_ASSIGNMENTS = 'scouting_assign_map_v1';
const STORAGE_KEY_SCOUT_PROFILE = 'scouting_manual_profile_v1';
const STORAGE_KEY_ACTIVE_ROOM = 'scouting_room_active_key_v1';

type MatchAssignmentKey = string; // format: matchKey:teamKey
type AssignmentMap = Record<MatchAssignmentKey, string>; // → scout name

type CompactMatch = {
  match_key: string;
  display_name: string;
  comp_level: string;
  set_number: number;
  match_number: number;
  red: string[]; // team keys
  blue: string[];
  is_completed: boolean;
};

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

const COMP_LEVEL_ORDER: Record<string, number> = { qm: 0, ef: 1, qf: 2, sf: 3, f: 4 };

function matchSortKey(m: CompactMatch): number {
  const level = COMP_LEVEL_ORDER[m.comp_level] ?? 9;
  return level * 1_000_000 + m.set_number * 1_000 + m.match_number;
}

function teamNum(teamKey: string): string {
  const match = /^frc(\d+)$/i.exec(teamKey);
  return match ? match[1] : teamKey;
}

function assignKey(matchKey: string, teamKey: string): MatchAssignmentKey {
  return `${matchKey}:${teamKey}`;
}

function normalizeScoutProfile(raw: string): string {
  return String(raw || '').trim().replace(/\s+/g, ' ');
}

function normalizeScoutProfileLookup(raw: string): string {
  return normalizeScoutProfile(raw).toLowerCase();
}

function assignmentMapFromRows(rows: ScoutingRoomAssignmentRecord[]): AssignmentMap {
  const map: AssignmentMap = {};
  for (const row of rows) {
    const matchKey = String(row.match_key || '').trim().toLowerCase();
    const teamKey = String(row.team_key || '').trim().toLowerCase();
    const assigned = normalizeScoutProfile(row.assigned_scout_profile);
    if (!matchKey || !teamKey || !assigned) continue;
    map[assignKey(matchKey, teamKey)] = assigned;
  }
  return map;
}

function presenceProfiles(members: ScoutingRoomPresenceMember[] | undefined): string[] {
  if (!Array.isArray(members) || members.length === 0) return [];
  const byLookup = new Map<string, string>();
  for (const member of members) {
    const profile = normalizeScoutProfile(member.scout_profile);
    const lookup = normalizeScoutProfileLookup(profile);
    if (!lookup || byLookup.has(lookup)) continue;
    byLookup.set(lookup, profile);
  }
  return Array.from(byLookup.values()).sort((a, b) => a.localeCompare(b));
}

function normalizeSecondaryLeaderProfiles(raw: unknown): string[] {
  if (!Array.isArray(raw) || raw.length === 0) return [];
  const byLookup = new Map<string, string>();
  for (const value of raw) {
    const profile = normalizeScoutProfile(String(value || ''));
    const lookup = normalizeScoutProfileLookup(profile);
    if (!lookup || byLookup.has(lookup)) continue;
    byLookup.set(lookup, profile);
  }
  return Array.from(byLookup.values()).sort((a, b) => a.localeCompare(b));
}

function loadScouts(): string[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY_SCOUTS);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

function saveScouts(scouts: string[]) {
  localStorage.setItem(STORAGE_KEY_SCOUTS, JSON.stringify(scouts));
}

function loadAssignments(): AssignmentMap {
  try {
    const raw = localStorage.getItem(STORAGE_KEY_ASSIGNMENTS);
    return raw ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}

function saveAssignments(map: AssignmentMap) {
  localStorage.setItem(STORAGE_KEY_ASSIGNMENTS, JSON.stringify(map));
}

function roomSyncErrorMessage(message: string, action: string): string {
  const detail = String(message || '').trim();
  if (!detail) return `${action} failed.`;
  const lookup = detail.toLowerCase();
  if (
    lookup.includes('aborterror')
    || lookup.includes('signal is aborted')
    || lookup.includes('operation was aborted')
    || lookup.includes('request timeout')
    || lookup.includes('timed out')
    || lookup.includes('without reason')
  ) {
    return `${action} timed out before the server replied. Check the API/backend and retry.`;
  }
  return detail;
}

/**
 * Auto-assign scouts to matches using round-robin across 6 stations.
 */
function autoAssign(
  matches: CompactMatch[],
  scouts: string[],
): AssignmentMap {
  if (scouts.length === 0) return {};
  const map: AssignmentMap = {};
  const upcoming = matches.filter((m) => !m.is_completed);
  let scoutIdx = 0;
  for (const match of upcoming) {
    const allTeams = [...match.red, ...match.blue];
    for (const teamKey of allTeams) {
      map[assignKey(match.match_key, teamKey)] = scouts[scoutIdx % scouts.length];
      scoutIdx++;
    }
  }
  return map;
}

const SCOUT_COLORS = [
  '#3b82f6', '#22c55e', '#eab308', '#ef4444', '#a855f7',
  '#ec4899', '#14b8a6', '#f97316', '#6366f1', '#84cc16',
];

function scoutColor(scout: string, scouts: string[]): string {
  const idx = scouts.indexOf(scout);
  return SCOUT_COLORS[idx % SCOUT_COLORS.length] || SCOUT_COLORS[0];
}

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

export function ScoutingAssignPage() {
  const isMobile = useMobileLayout();

  const { eventKey, eventInput, setEventInput, commitInput, selectEvent, fetchTrigger } = useEventKeyParam(STORAGE_KEY_EVENT);
  const [eventName, setEventName] = useState('');
  const [schedule, setSchedule] = useState<EventScheduleItem[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [errorText, setErrorText] = useState('');

  const [scouts, setScouts] = useState<string[]>(() => loadScouts());
  const [scoutInput, setScoutInput] = useState('');
  const [assignments, setAssignments] = useState<AssignmentMap>(() => loadAssignments());
  const [showCompleted, setShowCompleted] = useState(false);
  const [myAssignmentsOnly, setMyAssignmentsOnly] = useState(false);
  const [scoutProfile] = useState<string>(() => normalizeScoutProfile(localStorage.getItem(STORAGE_KEY_SCOUT_PROFILE) || ''));
  const [activeRoomKey] = useState<string>(() => String(sessionStorage.getItem(STORAGE_KEY_ACTIVE_ROOM) || '').trim().toLowerCase());
  const [roomClientId] = useState<string>(() => getOrCreateScoutingRoomClientId());
  const [roomMemberProfiles, setRoomMemberProfiles] = useState<string[]>([]);
  const [roomLeaderProfile, setRoomLeaderProfile] = useState<string>('');
  const [roomLeaderSource, setRoomLeaderSource] = useState<string>('');
  const [roomRole, setRoomRole] = useState<string>('');
  const [roomCreatorProfile, setRoomCreatorProfile] = useState<string>('');
  const [roomSecondaryLeaders, setRoomSecondaryLeaders] = useState<string[]>([]);
  const [roomSyncStatus, setRoomSyncStatus] = useState<string>('');
  const [roomSyncError, setRoomSyncError] = useState<string>('');
  const [roomAccessToken, setRoomAccessToken] = useState<string>(() => (
    activeRoomKey ? getStoredRoomAccessToken(activeRoomKey) : ''
  ));
  const roomAccessRefreshPromiseRef = useRef<Promise<string> | null>(null);
  const roomSyncEnabled = Boolean(activeRoomKey && scoutProfile);
  const isRoomLeader = useMemo(() => {
    if (!roomSyncEnabled) return false;
    const scoutLookup = normalizeScoutProfileLookup(scoutProfile);
    if (!scoutLookup) return false;
    if (String(roomRole || '').trim().toLowerCase() === 'owner') return true;
    if (normalizeScoutProfileLookup(roomCreatorProfile) === scoutLookup) return true;
    if (roomSecondaryLeaders.some((profile) => normalizeScoutProfileLookup(profile) === scoutLookup)) return true;
    return normalizeScoutProfileLookup(roomLeaderProfile) === scoutLookup;
  }, [roomCreatorProfile, roomLeaderProfile, roomRole, roomSecondaryLeaders, roomSyncEnabled, scoutProfile]);
  const canEditAssignments = !roomSyncEnabled || isRoomLeader;

  /* Save scouts and assignments to localStorage */
  useEffect(() => saveScouts(scouts), [scouts]);
  useEffect(() => saveAssignments(assignments), [assignments]);

  /* Fetch schedule */
  const fetchSchedule = useCallback(async (key: string) => {
    if (!key) return;
    setLoading(true);
    setErrorText('');
    try {
      const result = await getEventSchedule(key);
      setSchedule(result.matches);
      setEventName(result.event_name || key);
    } catch (err) {
      setErrorText((err as Error).message || 'Failed to load schedule.');
      setSchedule(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (eventKey) void fetchSchedule(eventKey);
  }, [eventKey, fetchSchedule, fetchTrigger]);

  useEffect(() => {
    if (!roomSyncEnabled) return;
    setMyAssignmentsOnly(true);
  }, [roomSyncEnabled]);

  useEffect(() => {
    if (!roomSyncEnabled) {
      setRoomMemberProfiles([]);
      setRoomLeaderProfile('');
      setRoomLeaderSource('');
      setRoomRole('');
      setRoomCreatorProfile('');
      setRoomSecondaryLeaders([]);
      setRoomSyncStatus('');
      setRoomSyncError('');
      return;
    }
    let cancelled = false;
    const loadFromRoom = async () => {
      try {
        const existingRoomAccessToken = getStoredRoomAccessToken(activeRoomKey);
        const joined = await createOrJoinScoutingRoom({
          room_key: activeRoomKey,
          event_key: eventKey || undefined,
          scout_profile: scoutProfile,
          client_id: roomClientId,
          title: eventKey ? `Scouting ${eventKey}` : undefined,
          create_if_missing: false,
          room_access_token: existingRoomAccessToken || undefined,
          timeoutMs: 25000,
        });
        if (cancelled) return;
        setRoomLeaderProfile(String(joined.room?.leader_scout_profile || ''));
        setRoomLeaderSource(String(joined.room?.leader_source || ''));
        setRoomRole(String(joined.access?.room_role || joined.room?.room_role || ''));
        setRoomCreatorProfile(normalizeScoutProfile(String(joined.room?.created_by || '')));
        setRoomSecondaryLeaders(normalizeSecondaryLeaderProfiles(joined.room?.secondary_leader_scout_profiles));
        setRoomMemberProfiles(presenceProfiles(joined.room?.presence));
        if (joined.access?.room_access_token) {
          setStoredRoomAccessToken(activeRoomKey, joined.access.room_access_token, joined.access.expires_at_unix);
          setRoomAccessToken(joined.access.room_access_token);
        } else {
          const fallback = getStoredRoomAccessToken(activeRoomKey);
          setRoomAccessToken(fallback);
        }
        const roomRows = Array.isArray(joined.assignments) ? joined.assignments : [];
        const scopedRows = eventKey
          ? roomRows.filter((row) => String(row.event_key || '').trim().toLowerCase() === eventKey.toLowerCase())
          : roomRows;
        setAssignments(assignmentMapFromRows(scopedRows));
        setScouts((previous) => {
          const next = new Set(previous.map((item) => normalizeScoutProfile(item)).filter(Boolean));
          for (const member of joined.room?.presence || []) {
            const profile = normalizeScoutProfile(member.scout_profile);
            if (profile) next.add(profile);
          }
          for (const row of scopedRows) {
            const profile = normalizeScoutProfile(row.assigned_scout_profile);
            if (profile) next.add(profile);
          }
          return Array.from(next).sort((a, b) => a.localeCompare(b));
        });
        setRoomSyncStatus(`Synced with room ${activeRoomKey}.`);
        setRoomSyncError('');
      } catch (error) {
        if (cancelled) return;
        setRoomSyncError(
          roomSyncErrorMessage(
            (error as Error).message || 'Unable to sync assignments from room.',
            'Syncing assignments from the scouting room',
          ),
        );
      }
    };
    void loadFromRoom();
    return () => {
      cancelled = true;
    };
  }, [activeRoomKey, eventKey, roomClientId, roomSyncEnabled, scoutProfile]);

  const refreshRoomAccessSession = useCallback(async (options?: { silent?: boolean }): Promise<string> => {
    if (!roomSyncEnabled) return '';
    const inFlight = roomAccessRefreshPromiseRef.current;
    if (inFlight) return inFlight;
    const run = (async () => {
      try {
        const existing = getStoredRoomAccessToken(activeRoomKey) || roomAccessToken;
        const joined = await createOrJoinScoutingRoom({
          room_key: activeRoomKey,
          event_key: eventKey || undefined,
          scout_profile: scoutProfile,
          client_id: roomClientId,
          title: eventKey ? `Scouting ${eventKey}` : undefined,
          create_if_missing: false,
          room_access_token: existing || undefined,
          timeoutMs: 25000,
        });
        const nextToken = String(joined.access?.room_access_token || '').trim();
        if (nextToken) {
          setStoredRoomAccessToken(activeRoomKey, nextToken, joined.access?.expires_at_unix);
          setRoomAccessToken(nextToken);
          setRoomSyncError('');
          if (!options?.silent) setRoomSyncStatus(`Room session refreshed for ${activeRoomKey}.`);
          return nextToken;
        }
      } catch (error) {
        if (!options?.silent) {
          setRoomSyncError(
            roomSyncErrorMessage(
              errorMessage(error) || 'Unable to refresh room session.',
              'Refreshing the scouting room session',
            ),
          );
        }
      } finally {
        roomAccessRefreshPromiseRef.current = null;
      }
      return '';
    })();
    roomAccessRefreshPromiseRef.current = run;
    return run;
  }, [activeRoomKey, eventKey, roomAccessToken, roomClientId, roomSyncEnabled, scoutProfile]);

  useEffect(() => {
    if (!roomSyncEnabled || !eventKey) return;
    let cancelled = false;
    const poll = async () => {
      try {
        const heartbeatToken = getStoredRoomAccessToken(activeRoomKey) || roomAccessToken;
        const response = await getScoutingRoomAssignments(activeRoomKey, {
          event_key: eventKey,
          for_scout_profile: scoutProfile,
          client_id: roomClientId,
          presence_heartbeat: true,
          room_access_token: heartbeatToken || undefined,
          timeoutMs: 30000,
        });
        if (cancelled) return;
        setRoomLeaderProfile(String(response.leader_scout_profile || ''));
        setRoomLeaderSource(String(response.leader_source || ''));
        setRoomRole(String(response.room_role || ''));
        setRoomSecondaryLeaders(normalizeSecondaryLeaderProfiles(response.secondary_leader_scout_profiles));
        setRoomMemberProfiles(presenceProfiles(response.presence));
        const rows = Array.isArray(response.assignments) ? response.assignments : [];
        setAssignments(assignmentMapFromRows(rows));
        setScouts((previous) => {
          const next = new Set(previous.map((item) => normalizeScoutProfile(item)).filter(Boolean));
          for (const member of response.presence || []) {
            const profile = normalizeScoutProfile(member.scout_profile);
            if (profile) next.add(profile);
          }
          for (const row of rows) {
            const assigned = normalizeScoutProfile(row.assigned_scout_profile);
            if (assigned) next.add(assigned);
          }
          return Array.from(next).sort((a, b) => a.localeCompare(b));
        });
      } catch (error) {
        const detail = errorMessage(error);
        if (isRoomAccessAuthError(detail)) {
          void refreshRoomAccessSession({ silent: true });
        }
        // keep local assignment state if polling fails
      }
    };
    void poll();
    const timer = window.setInterval(() => { void poll(); }, 4000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [activeRoomKey, eventKey, refreshRoomAccessSession, roomAccessToken, roomClientId, roomSyncEnabled, scoutProfile]);

  /* Derive match list */
  const matches = useMemo<CompactMatch[]>(() => {
    if (!schedule) return [];
    return schedule
      .map((m): CompactMatch => ({
        match_key: m.match_key,
        display_name: m.display_name || m.match_key,
        comp_level: m.comp_level,
        set_number: m.set_number,
        match_number: m.match_number,
        red: m.red.map((t) => t.team_key),
        blue: m.blue.map((t) => t.team_key),
        is_completed: m.is_completed ?? false,
      }))
      .sort((a, b) => matchSortKey(a) - matchSortKey(b));
  }, [schedule]);

  const visibleMatches = useMemo(() => {
    const base = showCompleted ? matches : matches.filter((m) => !m.is_completed);
    if (!myAssignmentsOnly || !scoutProfile) return base;
    const myLookup = normalizeScoutProfileLookup(scoutProfile);
    if (!myLookup) return base;
    return base.filter((match) => {
      const teams = [...match.red, ...match.blue];
      return teams.some((teamKey) => normalizeScoutProfileLookup(assignments[assignKey(match.match_key, teamKey)] || '') === myLookup);
    });
  }, [assignments, matches, myAssignmentsOnly, scoutProfile, showCompleted]);

  /* Coverage stats */
  const coverageStats = useMemo(() => {
    const upcoming = matches.filter((m) => !m.is_completed);
    let totalSlots = 0;
    let filledSlots = 0;
    for (const m of upcoming) {
      const allTeams = [...m.red, ...m.blue];
      totalSlots += allTeams.length;
      for (const t of allTeams) {
        if (assignments[assignKey(m.match_key, t)]) filledSlots++;
      }
    }
    return { totalSlots, filledSlots, coverage: totalSlots > 0 ? filledSlots / totalSlots : 0 };
  }, [matches, assignments]);

  /* Scout workload */
  const scoutWorkload = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const scout of scouts) counts[scout] = 0;
    for (const scout of Object.values(assignments)) {
      counts[scout] = (counts[scout] || 0) + 1;
    }
    return counts;
  }, [scouts, assignments]);

  /* Actions */

  function resolveRoomAccessToken(): string {
    if (!roomSyncEnabled) return '';
    if (roomAccessToken) return roomAccessToken;
    return getStoredRoomAccessToken(activeRoomKey);
  }

  function errorMessage(error: unknown): string {
    if (error instanceof Error && error.message) return error.message;
    return String(error || '').trim();
  }

  function isAbortLikeError(message: string): boolean {
    const lookup = String(message || '').trim().toLowerCase();
    if (!lookup) return false;
    return (
      lookup.includes('aborterror')
      || lookup.includes('signal is aborted')
      || lookup.includes('operation was aborted')
      || lookup.includes('request timeout')
      || lookup.includes('timed out')
    );
  }

  function isRoomAccessAuthError(message: string): boolean {
    const lookup = String(message || '').trim().toLowerCase();
    if (!lookup) return false;
    return (
      lookup.includes('x-room-access-token')
      || lookup.includes('room access token')
      || lookup.includes('authorization failed')
      || lookup.includes('requires a valid')
    );
  }

  async function ensureRoomAccessToken(): Promise<string> {
    const token = resolveRoomAccessToken();
    if (token) return token;
    return refreshRoomAccessSession({ silent: true });
  }

  async function syncSingleAssignmentToRoom(
    matchKey: string,
    teamKey: string,
    assignedScoutProfile?: string | null,
  ): Promise<boolean> {
    if (!roomSyncEnabled) return true;
    let token = await ensureRoomAccessToken();
    if (!token) {
      setRoomSyncError('Room access token missing. Rejoin the scouting room from Live Scouting.');
      return false;
    }
    for (let attempt = 1; attempt <= 3; attempt++) {
      try {
        await upsertScoutingRoomAssignment(activeRoomKey, {
          event_key: eventKey || undefined,
          match_key: matchKey,
          team_key: teamKey,
          assigned_scout_profile: assignedScoutProfile ?? null,
          room_access_token: token,
          timeoutMs: 45000,
        });
        setRoomSyncError('');
        return true;
      } catch (error) {
        const detail = errorMessage(error) || 'Failed to sync assignment to room.';
        if (isRoomAccessAuthError(detail) && attempt < 3) {
          token = await refreshRoomAccessSession({ silent: true });
          if (token) continue;
        }
        if (isAbortLikeError(detail)) {
          if (attempt < 3) {
            await new Promise((resolve) => window.setTimeout(resolve, 250 * attempt));
            continue;
          }
          // Keep local assignment to avoid data loss when network times out.
          setRoomSyncError('Room sync delayed by network timeout. Assignment kept locally.');
          return true;
        }
        setRoomSyncError(detail);
        return false;
      }
    }
    return false;
  }

  async function syncAssignmentMapToRoom(map: AssignmentMap): Promise<boolean> {
    if (!roomSyncEnabled) return true;
    if (!eventKey) {
      setRoomSyncError('Select an event before syncing room assignments.');
      return false;
    }
    let token = await ensureRoomAccessToken();
    if (!token) {
      setRoomSyncError('Room access token missing. Rejoin the scouting room from Live Scouting.');
      return false;
    }
    const rows = Object.entries(map).map(([key, assignedScout]) => {
      const [matchKey, teamKey] = key.split(':');
      return {
        match_key: matchKey,
        team_key: teamKey,
        assigned_scout_profile: assignedScout,
      };
    });
    for (let attempt = 1; attempt <= 3; attempt++) {
      try {
        await replaceScoutingRoomAssignments(activeRoomKey, {
          event_key: eventKey,
          assignments: rows,
          room_access_token: token,
          timeoutMs: 60000,
        });
        setRoomSyncError('');
        return true;
      } catch (error) {
        const detail = errorMessage(error) || 'Failed to sync room assignments.';
        if (isRoomAccessAuthError(detail) && attempt < 3) {
          token = await refreshRoomAccessSession({ silent: true });
          if (token) continue;
        }
        if (isAbortLikeError(detail)) {
          if (attempt < 3) {
            await new Promise((resolve) => window.setTimeout(resolve, 250 * attempt));
            continue;
          }
          setRoomSyncError('Room sync delayed by network timeout. Assignments were kept locally.');
          return true;
        }
        setRoomSyncError(detail);
        return false;
      }
    }
    return false;
  }

  function addScout() {
    if (!canEditAssignments) return;
    const name = normalizeScoutProfile(scoutInput);
    if (!name) return;
    if (scouts.some((item) => normalizeScoutProfileLookup(item) === normalizeScoutProfileLookup(name))) {
      setScoutInput('');
      return;
    }
    setScouts((current) => [...current, name].sort((a, b) => a.localeCompare(b)));
    setScoutInput('');
  }

  function addScoutFromRoom(profile: string) {
    if (!canEditAssignments) return;
    const name = normalizeScoutProfile(profile);
    if (!name) return;
    setScouts((current) => {
      if (current.some((item) => normalizeScoutProfileLookup(item) === normalizeScoutProfileLookup(name))) {
        return current;
      }
      return [...current, name].sort((a, b) => a.localeCompare(b));
    });
  }

  function removeScout(name: string) {
    if (!canEditAssignments) return;
    setScouts(scouts.filter((s) => s !== name));
    // Remove all their assignments
    const next: AssignmentMap = {};
    for (const [key, val] of Object.entries(assignments)) {
      if (val !== name) next[key] = val;
    }
    setAssignments(next);
    void (async () => {
      if (!roomSyncEnabled || !eventKey) return;
      await syncAssignmentMapToRoom(next);
    })();
  }

  function assignScout(matchKey: string, teamKey: string, scout: string) {
    if (!canEditAssignments) return;
    const key = assignKey(matchKey, teamKey);
    const prior = assignments[key] || '';
    setAssignments((prev) => ({ ...prev, [key]: scout }));
    void (async () => {
      const ok = await syncSingleAssignmentToRoom(matchKey, teamKey, scout);
      if (!ok) {
        setAssignments((prev) => {
          const next = { ...prev };
          if (prior) next[key] = prior;
          else delete next[key];
          return next;
        });
      }
    })();
  }

  function clearAssignment(matchKey: string, teamKey: string) {
    if (!canEditAssignments) return;
    const key = assignKey(matchKey, teamKey);
    const prior = assignments[key] || '';
    setAssignments((prev) => {
      const next = { ...prev };
      delete next[key];
      return next;
    });
    void (async () => {
      const ok = await syncSingleAssignmentToRoom(matchKey, teamKey, null);
      if (!ok && prior) {
        setAssignments((prev) => ({ ...prev, [key]: prior }));
      }
    })();
  }

  function runAutoAssign() {
    if (!canEditAssignments) return;
    const map = autoAssign(matches, scouts);
    setAssignments(map);
    void (async () => {
      if (!roomSyncEnabled) return;
      if (!eventKey) {
        setRoomSyncError('Select an event before syncing auto-assignments to room.');
        return;
      }
      await syncAssignmentMapToRoom(map);
    })();
  }

  function clearAllAssignments() {
    if (!canEditAssignments) return;
    setAssignments({});
    void (async () => {
      if (!roomSyncEnabled) return;
      if (!eventKey) {
        setRoomSyncError('Select an event before clearing room assignments.');
        return;
      }
      await syncAssignmentMapToRoom({});
    })();
  }

  const surfaceGroupId = 'scouting-assignments';

  return (
    <>
    <PageViewBar items={SCOUTING_VIEWS} className="scouting-page-view-bar" collapseToMenuOnMobile />
    <div className="scouting-layout-grid">
    <div className="center-page-container">
      <SurfaceCardGroup groupId={surfaceGroupId}>
        {/* ---- Event Selection ---- */}
        <SurfaceCard
          title="Scouting Assignments"
          subtitle="Assign scouts to matches."
        >
          <EventPicker
            value={eventKey}
            onSelect={selectEvent}
            inputValue={eventInput}
            onInputChange={setEventInput}
            onSubmit={commitInput}
            loading={loading}
          />

          {eventName && schedule ? (
            <p className="center-event-status">
              <strong>{eventName}</strong> — {matches.length} matches
            </p>
          ) : null}

          {roomSyncEnabled ? (
            <p className="center-callout muted">
              Room sync active: <strong>{activeRoomKey}</strong> as <strong>{scoutProfile}</strong>.
              {' '}
              Leader: <strong>{roomLeaderProfile || 'unknown'}</strong>
              {roomLeaderSource ? ` (${roomLeaderSource.replace(/_/g, ' ')})` : ''}.
              {roomSecondaryLeaders.length > 0 ? ` Secondary leaders: ${roomSecondaryLeaders.join(', ')}.` : ''}
            </p>
          ) : (
            <p className="center-callout muted">
              Room sync is off. Set a scout profile and join a room in Live Scouting to share assignments.
            </p>
          )}
          {roomSyncStatus ? <p className="center-callout muted">{roomSyncStatus}</p> : null}
          {roomSyncError ? <p className="center-callout warning">{roomSyncError}</p> : null}
          {roomSyncEnabled && !canEditAssignments ? (
            <p className="center-callout muted">
              You are not a room leader, so this page is read-only.
            </p>
          ) : null}

          {errorText ? <p className="center-callout warning">{errorText}</p> : null}
        </SurfaceCard>

        {/* ---- Scout Roster ---- */}
        <SurfaceCard
          title="Scout Roster"
          subtitle="Add your scouts."
          right={<span className="center-chip">{scouts.length} scouts</span>}
        >
          <div className="center-input-row" style={{ marginBottom: '0.5rem' }}>
            <input
              className="center-input"
              value={scoutInput}
              onChange={(e) => setScoutInput(e.target.value)}
              placeholder="Scout name"
              onKeyDown={(e) => e.key === 'Enter' && addScout()}
              style={{ flex: 1 }}
              disabled={!canEditAssignments}
            />
            <button type="button" className="center-btn" onClick={addScout} disabled={!canEditAssignments}>
              Add Scout
            </button>
          </div>

          {roomSyncEnabled && roomMemberProfiles.length > 0 ? (
            <div style={{ marginBottom: '0.6rem' }}>
              <p className="center-event-status text-muted">Tap a room member to add to roster:</p>
              <div className="center-actions-row compact">
                {roomMemberProfiles.map((profile) => {
                  const alreadyAdded = scouts.some(
                    (item) => normalizeScoutProfileLookup(item) === normalizeScoutProfileLookup(profile),
                  );
                  return (
                    <button
                      key={`room-member-add-${profile}`}
                      type="button"
                      className="center-btn ghost"
                      onClick={() => addScoutFromRoom(profile)}
                      disabled={!canEditAssignments || alreadyAdded}
                      title={alreadyAdded ? `${profile} already in roster` : `Add ${profile} to roster`}
                    >
                      {profile}{alreadyAdded ? ' (added)' : ''}
                    </button>
                  );
                })}
              </div>
            </div>
          ) : null}

          {scouts.length > 0 ? (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.35rem' }}>
              {scouts.map((scout) => (
                <span
                  key={scout}
                  className="center-chip"
                  style={{
                    background: scoutColor(scout, scouts),
                    color: '#fff',
                    fontWeight: 600,
                  }}
                >
                  {scout}
                  <span className="text-sm" style={{ opacity: 0.7 }}>
                    ({scoutWorkload[scout] || 0})
                  </span>
                  <button
                    type="button"
                    className="scout-chip-remove"
                    onClick={() => removeScout(scout)}
                    title="Remove scout"
                    disabled={!canEditAssignments}
                  >
                    x
                  </button>
                </span>
              ))}
            </div>
          ) : (
            <p className="center-event-status text-muted">
              Add scouts above to begin.
            </p>
          )}
        </SurfaceCard>

        {/* ---- Coverage Overview ---- */}
        {matches.length > 0 && scouts.length > 0 ? (
          <SurfaceCard title="Coverage" subtitle="Assignment coverage for upcoming matches.">
            <div className="center-kpi-grid">
              <div className={`center-kpi-card ${coverageStats.coverage >= 0.9 ? 'tone-green' : coverageStats.coverage >= 0.5 ? 'tone-yellow' : 'tone-red'}`}>
                <span>Coverage</span>
                <strong>
                  {(coverageStats.coverage * 100).toFixed(0)}%
                </strong>
              </div>
              <div className="center-kpi-card">
                <span>Assigned</span>
                <strong>
                  {coverageStats.filledSlots} / {coverageStats.totalSlots}
                </strong>
              </div>
              <div className="center-kpi-card">
                <span>Scouts</span>
                <strong>{scouts.length}</strong>
              </div>
              <div className="center-kpi-card">
                <span>Matches</span>
                <strong>{matches.filter((m) => !m.is_completed).length}</strong>
              </div>
            </div>

            {/* Progress bar */}
            <div className="center-progress-track">
              <div
                className={`center-progress-fill ${coverageStats.coverage >= 0.9 ? 'tone-green' : coverageStats.coverage >= 0.5 ? 'tone-yellow' : 'tone-red'}`}
                style={{ width: `${coverageStats.coverage * 100}%` }}
              />
            </div>

            <div className="center-actions-row" style={{ marginTop: '0.75rem' }}>
              <button
                type="button"
                className="center-btn"
                onClick={runAutoAssign}
                disabled={scouts.length === 0 || !canEditAssignments}
              >
                Auto-Assign All
              </button>
              <button type="button" className="center-btn ghost" onClick={clearAllAssignments} disabled={!canEditAssignments}>
                Clear All
              </button>
              <label className="center-checkbox-label">
                <input
                  type="checkbox"
                  checked={showCompleted}
                  onChange={(e) => setShowCompleted(e.target.checked)}
                />
                Show completed
              </label>
              <label className="center-checkbox-label">
                <input
                  type="checkbox"
                  checked={myAssignmentsOnly}
                  onChange={(e) => setMyAssignmentsOnly(e.target.checked)}
                  disabled={!scoutProfile}
                />
                Only my assignments
              </label>
            </div>
          </SurfaceCard>
        ) : null}

        {/* ---- Match Assignments ---- */}
        {visibleMatches.length > 0 && scouts.length > 0 ? (
          <SurfaceCard
            title="Match Assignments"
            subtitle={`${visibleMatches.length} matches. ${!canEditAssignments ? 'Read-only view.' : isMobile ? 'Tap to assign scouts.' : 'Click a cell to assign a scout.'}`}
          >
            {/* Desktop table */}
            <div className="center-table-wrap desktop-only">
              <table className="center-table">
                <thead>
                  <tr>
                    <th style={{ minWidth: 80 }}>Match</th>
                    <th colSpan={3} className="text-red" style={{ textAlign: 'center' }}>
                      Red Alliance
                    </th>
                    <th colSpan={3} className="text-blue" style={{ textAlign: 'center' }}>
                      Blue Alliance
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {visibleMatches.map((match) => (
                    <MatchAssignmentRow
                      key={match.match_key}
                      match={match}
                      scouts={scouts}
                      assignments={assignments}
                      onAssign={assignScout}
                      onClear={clearAssignment}
                      editable={canEditAssignments}
                    />
                  ))}
                </tbody>
              </table>
            </div>

            {/* Mobile card list */}
            <div className="assign-mobile-list mobile-only">
              {visibleMatches.map((match) => (
                <MobileAssignmentCard
                  key={`m-${match.match_key}`}
                  match={match}
                  scouts={scouts}
                  assignments={assignments}
                  onAssign={assignScout}
                  onClear={clearAssignment}
                  editable={canEditAssignments}
                />
              ))}
            </div>
          </SurfaceCard>
        ) : null}
      </SurfaceCardGroup>
    </div>
    </div>
    </>
  );
}

/* ------------------------------------------------------------------ */
/*  Match Assignment Row                                                */
/* ------------------------------------------------------------------ */

function MatchAssignmentRow({
  match,
  scouts,
  assignments,
  onAssign,
  onClear,
  editable,
}: {
  match: CompactMatch;
  scouts: string[];
  assignments: AssignmentMap;
  onAssign: (matchKey: string, teamKey: string, scout: string) => void;
  onClear: (matchKey: string, teamKey: string) => void;
  editable: boolean;
}) {
  const allTeams = [...match.red, ...match.blue];

  return (
    <tr className={match.is_completed ? 'assign-row-completed' : undefined}>
      <td style={{ fontWeight: 600, whiteSpace: 'nowrap', fontSize: '0.85rem' }}>
        {match.display_name}
      </td>
      {allTeams.map((teamKey, idx) => {
        const key = assignKey(match.match_key, teamKey);        const assigned = assignments[key] || '';
        return (
          <td
            key={teamKey}
            style={{
              padding: '0.25rem 0.35rem',
              borderLeft: idx === 3 ? '2px solid var(--surface-border, #444)' : undefined,
            }}
          >
            <div className="assign-cell-team-num">
              {teamNum(teamKey)}
            </div>
            <select
              className={`assign-cell-select${assigned ? ' assigned' : ''}`}
              value={assigned}
              onChange={(e) => {
                const val = e.target.value;
                if (val) onAssign(match.match_key, teamKey, val);
                else onClear(match.match_key, teamKey);
              }}
              style={assigned ? { background: scoutColor(assigned, scouts) } : undefined}
              disabled={match.is_completed || !editable}
            >
              <option value="">—</option>
              {scouts.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </td>
        );
      })}
    </tr>
  );
}

/* ------------------------------------------------------------------ */
/*  Mobile Assignment Card                                             */
/* ------------------------------------------------------------------ */

function MobileAssignmentCard({
  match,
  scouts,
  assignments,
  onAssign,
  onClear,
  editable,
}: {
  match: CompactMatch;
  scouts: string[];
  assignments: AssignmentMap;
  onAssign: (matchKey: string, teamKey: string, scout: string) => void;
  onClear: (matchKey: string, teamKey: string) => void;
  editable: boolean;
}) {
  function renderTeamRow(teamKey: string) {
    const key = assignKey(match.match_key, teamKey);
    const assigned = assignments[key] || '';
    return (
      <div className="assign-mobile-team-row" key={teamKey}>
        <span className="assign-mobile-team-num">{teamNum(teamKey)}</span>
        <select
          className={assigned ? 'assigned' : ''}
          value={assigned}
          onChange={(e) => {
            const val = e.target.value;
            if (val) onAssign(match.match_key, teamKey, val);
            else onClear(match.match_key, teamKey);
          }}
          style={assigned ? { background: scoutColor(assigned, scouts), color: '#fff' } : undefined}
          disabled={match.is_completed || !editable}
        >
          <option value="">—</option>
          {scouts.map((s) => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>
      </div>
    );
  }

  return (
    <div className={`assign-mobile-card${match.is_completed ? ' assign-mobile-completed' : ''}`}>
      <div className="assign-mobile-card-head">
        <span>{match.display_name}</span>
        {match.is_completed && <span className="text-muted" style={{ fontSize: '0.75rem' }}>Completed</span>}
      </div>
      <div className="assign-mobile-alliance">
        <div className="assign-mobile-alliance-label red">Red Alliance</div>
        {match.red.map(renderTeamRow)}
      </div>
      <div className="assign-mobile-divider" />
      <div className="assign-mobile-alliance">
        <div className="assign-mobile-alliance-label blue">Blue Alliance</div>
        {match.blue.map(renderTeamRow)}
      </div>
    </div>
  );
}
