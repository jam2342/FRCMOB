import { useMemo, useState } from 'react';
import type { PitScoutingEntry } from '../../api';
import { SurfaceCard } from '../../components/ui/SurfaceCard';
import { Button } from '../../components/ui/primitives';
import { ListIcon } from '../../components/ui/Icons';
import { useWorkspace } from '../workspace/useWorkspace';
import { PitQueue } from './PitQueue';
import { buildQueueRows, teamNumber } from './pitTeams';
import type { PitTeam } from './pitTeams';
import { usePitAssignments } from './usePitAssignments';
import { usePitLocalState } from './usePitLocalState';
import queueStyles from './PitQueue.module.css';
import './pit.css';

export type PitTeamPickerProps = {
  eventKey: string;
  teams: PitTeam[];
  entriesByTeam: Map<string, PitScoutingEntry>;
  selectedTeam: string;
  // The open form has edits not yet saved, which means a draft on this phone.
  formDirty: boolean;
  // Picking a team, saving and leaving all wait for a save or upload in flight.
  disabled: boolean;
  // On a phone the queue folds away behind "Change team" once a team is open.
  collapsible: boolean;
  onSelect: (teamKey: string) => void;
};

export function PitTeamPicker({ eventKey, teams, entriesByTeam, selectedTeam, formDirty, disabled, collapsible, onSelect }: PitTeamPickerProps) {
  const session = useWorkspace();
  const [chooserOpen, setChooserOpen] = useState(false);
  const meId = session?.me.id ?? null;
  const isLeader = session?.me.role === 'leader';
  const assignments = usePitAssignments({
    eventKey,
    me: session ? { id: session.me.id, display_name: session.me.display_name } : null,
    enabled: Boolean(session) && teams.length > 0,
  });
  const local = usePitLocalState(session?.workspace.id ?? 0, eventKey, teams, entriesByTeam, selectedTeam, formDirty);
  const rows = useMemo(
    () => buildQueueRows(teams, entriesByTeam, assignments.byTeam, local, meId),
    [teams, entriesByTeam, assignments.byTeam, local, meId],
  );

  if (teams.length === 0) return null;

  if (collapsible && selectedTeam && !chooserOpen) {
    const row = rows.find((item) => item.team.team_key === selectedTeam);
    const nickname = teams.find((team) => team.team_key === selectedTeam)?.nickname ?? null;
    const busy = assignments.pending.has(selectedTeam);
    const blocked = !assignments.online;
    return (
      <>
        <div className="pit-selected-team">
          <div className="pit-selected-team__who">
            <strong>Team {teamNumber(selectedTeam)}</strong>
            {nickname ? <span title={nickname}>{nickname}</span> : null}
            {row && !row.done && row.assigneeId !== null ? (
              <small className={queueStyles.openTeam}>{row.mine ? "You're on it" : `${row.assigneeName} is on it`}</small>
            ) : null}
          </div>
          {row && !row.done && row.assigneeId === null ? (
            <Button
              size="sm"
              variant="quiet"
              loading={busy}
              disabled={blocked || !assignments.ready}
              title={blocked ? "Offline: reconnect to change who's on a team" : undefined}
              onClick={() => void assignments.claim(selectedTeam)}
            >
              I'm on it
            </Button>
          ) : null}
          {row && !row.done && row.mine ? (
            <Button
              size="sm"
              variant="quiet"
              loading={busy}
              disabled={blocked}
              title={blocked ? "Offline: reconnect to change who's on a team" : undefined}
              onClick={() => void assignments.release(selectedTeam)}
            >
              Release
            </Button>
          ) : null}
          <Button variant="quiet" size="sm" icon={<ListIcon />} onClick={() => setChooserOpen(true)} disabled={disabled}>
            Change team
          </Button>
        </div>
        {assignments.notice ? (
          <p className={queueStyles.banner} role="status">
            <span>{assignments.notice.text}</span>
            <button type="button" className={queueStyles.dismiss} onClick={assignments.dismissNotice}>Dismiss</button>
          </p>
        ) : null}
      </>
    );
  }

  return (
    <SurfaceCard
      tourId="pit-teams"
      title="Teams"
      subtitle={collapsible ? undefined : "Tap a team to fill out its pit form. Tap I'm on it so two scouts don't walk to the same pit."}
      mobileCollapsible={false}
      hideTitleOnPhone
    >
      <PitQueue
        rows={rows}
        selectedTeam={selectedTeam}
        disabled={disabled}
        assignments={assignments}
        isLeader={isLeader}
        meId={meId}
        compact={collapsible}
        onOpen={(teamKey) => {
          onSelect(teamKey);
          setChooserOpen(false);
        }}
      />
    </SurfaceCard>
  );
}
