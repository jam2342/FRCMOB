import { useEffect, useMemo, useState } from 'react';
import { listQueuedMutations } from '../../utils/offlineQueue';
import { readPitDraft } from '../offline/pitDrafts';
import type { PitScoutingEntry } from '../../api';
import type { PitLocalState, PitTeam } from './pitTeams';

async function queuedTeams(eventKey: string): Promise<Set<string>> {
  const queued = new Set<string>();
  for (const item of await listQueuedMutations()) {
    if (item.method !== 'POST' || !/\/pit-scouting\/?$/.test(String(item.url ?? '').split('?')[0])) continue;
    try {
      const body = JSON.parse(item.body || '{}') as { event_key?: string; team_key?: string };
      if (body.event_key === eventKey && body.team_key) queued.add(body.team_key.toLowerCase());
    } catch {
      // A body we can't read isn't a save we can name.
    }
  }
  return queued;
}

// Entries, the open team and the form's dirty flag are the moments a draft is written or cleared, so they re-read storage.
export function usePitLocalState(
  workspaceId: number,
  eventKey: string,
  teams: PitTeam[],
  entries: Map<string, PitScoutingEntry>,
  selectedTeam: string,
  formDirty: boolean,
): PitLocalState {
  const [queued, setQueued] = useState<ReadonlySet<string>>(new Set());
  const [queueTick, setQueueTick] = useState(0);

  useEffect(() => {
    const bump = () => setQueueTick((tick) => tick + 1);
    window.addEventListener('offlinequeue:change', bump);
    window.addEventListener('offlinequeue:delivered', bump);
    return () => {
      window.removeEventListener('offlinequeue:change', bump);
      window.removeEventListener('offlinequeue:delivered', bump);
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    void queuedTeams(eventKey).then((next) => { if (!cancelled) setQueued(next); }, () => undefined);
    return () => { cancelled = true; };
  }, [eventKey, queueTick]);

  const drafts = useMemo(
    () => new Set(teams.filter((team) => readPitDraft(workspaceId, eventKey, team.team_key) !== null).map((team) => team.team_key)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [workspaceId, eventKey, teams, queueTick, entries, selectedTeam, formDirty],
  );
  return useMemo(() => ({ drafts, queued }), [drafts, queued]);
}
