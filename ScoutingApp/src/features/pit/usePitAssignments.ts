import { useCallback, useEffect, useRef, useState } from 'react';
import { autoSplitPitTeams, claimPitTeam, getPitAssignments, releasePitTeam, setPitAssignment } from '../../api';
import type { PitAssignment } from '../../api';
import { usePageVisibility } from '../../hooks/usePageVisibility';
import { useOnlineStatus } from '../../hooks/useOnlineStatus';
import { useSingleFlightPolling } from '../../hooks/useSingleFlightPolling';

export const PIT_ASSIGNMENT_POLL_MS = 20_000;

export type PitAssignmentNotice = { tone: 'info' | 'warn'; text: string };
type Me = { id: number; display_name: string };

export type PitAssignments = {
  byTeam: Map<string, PitAssignment>;
  // True once a load has answered; until then every team looks unassigned only because we don't know yet.
  ready: boolean;
  online: boolean;
  pending: ReadonlySet<string>;
  notice: PitAssignmentNotice | null;
  dismissNotice: () => void;
  claim: (teamKey: string, opts?: { force?: boolean }) => Promise<boolean>;
  release: (teamKey: string) => Promise<boolean>;
  reassign: (teamKey: string, member: { id: number; name: string } | null) => Promise<boolean>;
  autoSplit: (teamKeys: string[], memberIds: number[]) => Promise<PitAssignment[] | null>;
  refresh: () => void;
};

function toMap(rows: PitAssignment[]): Map<string, PitAssignment> {
  return new Map(rows.filter((row) => row.member_id !== null).map((row) => [row.team_key.toLowerCase(), row]));
}

// The server names the team by key ("frc1912 is already taken by Alex."); the app says "Team 1912".
export function friendlyTeamMessage(text: string): string {
  return text.replace(/^frc(\d+[a-z]?)\b/i, 'Team $1');
}

function messageOf(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? friendlyTeamMessage(error.message) : fallback;
}

export function usePitAssignments({ eventKey, me, enabled }: { eventKey: string; me: Me | null; enabled: boolean }): PitAssignments {
  const visible = usePageVisibility();
  const { online } = useOnlineStatus();
  const [byTeam, setByTeam] = useState<Map<string, PitAssignment>>(new Map());
  const [ready, setReady] = useState(false);
  const [pending, setPending] = useState<ReadonlySet<string>>(new Set());
  const [notice, setNotice] = useState<PitAssignmentNotice | null>(null);
  // Bumped by every local change, so a poll that began before it can't put the old answer back.
  const changeSeq = useRef(0);
  const eventRef = useRef(eventKey);
  eventRef.current = eventKey;
  const byTeamRef = useRef(byTeam);
  byTeamRef.current = byTeam;

  useEffect(() => {
    setByTeam(new Map());
    setReady(false);
    setPending(new Set());
    setNotice(null);
  }, [eventKey]);

  const load = useCallback(async (): Promise<boolean> => {
    const forEvent = eventRef.current;
    const seq = changeSeq.current;
    try {
      const rows = await getPitAssignments(forEvent);
      if (eventRef.current !== forEvent || changeSeq.current !== seq) return true;
      setByTeam(toMap(rows));
      setReady(true);
      return true;
    } catch {
      return false;
    }
  }, []);

  const { triggerNow } = useSingleFlightPolling({
    enabled: enabled && Boolean(eventKey) && online,
    visible,
    intervalMs: PIT_ASSIGNMENT_POLL_MS,
    run: load,
  });
  const refresh = useCallback(() => triggerNow('manual'), [triggerNow]);

  const mark = (teamKey: string, busy: boolean) =>
    setPending((current) => {
      const next = new Set(current);
      if (busy) next.add(teamKey);
      else next.delete(teamKey);
      return next;
    });

  const put = (teamKey: string, row: PitAssignment | null) => {
    changeSeq.current += 1;
    setByTeam((current) => {
      const next = new Map(current);
      if (row) next.set(teamKey, row);
      else next.delete(teamKey);
      return next;
    });
  };

  const offlineNotice = (what: string): false => {
    setNotice({ tone: 'warn', text: `You're offline, so ${what} has to wait. Nothing was changed.` });
    return false;
  };

  // One shape for every write: show the result now, undo it if the server says no, then re-read.
  async function change(
    teamKey: string,
    optimistic: PitAssignment | null,
    send: () => Promise<PitAssignment | null | undefined>,
    failure: string,
  ): Promise<boolean> {
    const before = byTeamRef.current.get(teamKey) ?? null;
    mark(teamKey, true);
    put(teamKey, optimistic);
    setNotice(null);
    try {
      const saved = await send();
      put(teamKey, saved ?? optimistic);
      return true;
    } catch (error) {
      put(teamKey, before);
      setNotice({ tone: 'warn', text: messageOf(error, failure) });
      return false;
    } finally {
      mark(teamKey, false);
      refresh();
    }
  }

  const claim = async (teamKey: string, opts?: { force?: boolean }): Promise<boolean> => {
    if (!me) return false;
    if (!online) return offlineNotice('taking a team');
    const mine: PitAssignment = {
      team_key: teamKey,
      member_id: me.id,
      member_name: me.display_name,
      source: 'claim',
      assigned_by_member_id: me.id,
      updated_at: new Date().toISOString(),
    };
    return change(teamKey, mine, async () => (await claimPitTeam(eventKey, teamKey, opts)).assignment, "Couldn't take that team. Try again.");
  };

  const release = async (teamKey: string): Promise<boolean> => {
    if (!online) return offlineNotice('letting a team go');
    return change(teamKey, null, async () => (await releasePitTeam(eventKey, teamKey), null), "Couldn't let that team go. Try again.");
  };

  const reassign = async (teamKey: string, member: { id: number; name: string } | null): Promise<boolean> => {
    if (!online) return offlineNotice('reassigning');
    const next: PitAssignment | null = member
      ? {
          team_key: teamKey,
          member_id: member.id,
          member_name: member.name,
          source: 'leader',
          assigned_by_member_id: me?.id ?? null,
          updated_at: new Date().toISOString(),
        }
      : null;
    return change(
      teamKey,
      next,
      async () => (await setPitAssignment(eventKey, teamKey, member?.id ?? null)).assignment,
      "Couldn't change that assignment. Try again.",
    );
  };

  const autoSplit = async (teamKeys: string[], memberIds: number[]): Promise<PitAssignment[] | null> => {
    if (!online) { offlineNotice('splitting teams'); return null; }
    changeSeq.current += 1;
    try {
      const rows = await autoSplitPitTeams(eventKey, { teamKeys, memberIds, onlyUnscouted: true });
      changeSeq.current += 1;
      setByTeam(toMap(rows));
      setReady(true);
      return rows;
    } catch (error) {
      setNotice({ tone: 'warn', text: messageOf(error, "Couldn't split the teams. Try again.") });
      return null;
    } finally {
      refresh();
    }
  };

  return {
    byTeam,
    ready,
    online,
    pending,
    notice,
    dismissNotice: () => setNotice(null),
    claim,
    release,
    reassign,
    autoSplit,
    refresh,
  };
}
