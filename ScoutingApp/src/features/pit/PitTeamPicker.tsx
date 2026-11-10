import { useMemo, useState } from 'react';
import type { PitScoutingEntry } from '../../api';
import { SurfaceCard } from '../../components/ui/SurfaceCard';
import { Button, FieldCheckbox, FieldText } from '../../components/ui/primitives';
import { CameraIcon, CheckIcon, ListIcon } from '../../components/ui/Icons';
import { filterPitTeams, hasPitEntry, teamNumber } from './pitTeams';
import type { PitTeam } from './pitTeams';
import './pit.css';

export type PitTeamPickerProps = {
  teams: PitTeam[];
  entriesByTeam: Map<string, PitScoutingEntry>;
  selectedTeam: string;
  // Picking a team, saving and leaving all wait for a save or upload in flight.
  disabled: boolean;
  // On a phone the grid folds away behind "Change team" once a team is open.
  collapsible: boolean;
  onSelect: (teamKey: string) => void;
};

export function PitTeamPicker({ teams, entriesByTeam, selectedTeam, disabled, collapsible, onSelect }: PitTeamPickerProps) {
  const [chooserOpen, setChooserOpen] = useState(false);
  const [query, setQuery] = useState('');
  // On by default: a scout walking the pits wants the robots still to do, not the ones already done.
  const [unscoutedOnly, setUnscoutedOnly] = useState(true);

  const visible = useMemo(
    () => filterPitTeams(teams, entriesByTeam, { query, unscoutedOnly, keepKey: selectedTeam }),
    [teams, entriesByTeam, query, unscoutedOnly, selectedTeam],
  );

  if (teams.length === 0) return null;

  if (collapsible && selectedTeam && !chooserOpen) {
    const nickname = teams.find((team) => team.team_key === selectedTeam)?.nickname ?? null;
    return (
      <div className="pit-selected-team">
        <div className="pit-selected-team__who">
          <strong>Team {teamNumber(selectedTeam)}</strong>
          {nickname ? <span title={nickname}>{nickname}</span> : null}
        </div>
        <Button size="sm" icon={<ListIcon />} onClick={() => setChooserOpen(true)} disabled={disabled}>
          Change team
        </Button>
      </div>
    );
  }

  return (
    <SurfaceCard
      tourId="pit-teams"
      title="Teams"
      subtitle="Tap a team to fill out its pit form. A check means it's scouted; a camera means it has photos."
      mobileCollapsible={false}
    >
      <div className="pit-picker-tools">
        <FieldText
          label="Find a team"
          type="search"
          inputMode="search"
          enterKeyHint="search"
          autoComplete="off"
          placeholder="Number or name"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <FieldCheckbox
          label="Unscouted only"
          checked={unscoutedOnly}
          onChange={(event) => setUnscoutedOnly(event.target.checked)}
        />
      </div>
      {visible.length === 0 ? (
        <p className="pit-picker-empty" role="status">
          {query.trim() ? `No team matches "${query.trim()}".` : 'Every team has notes. Untick "Unscouted only" to review them.'}
        </p>
      ) : (
        <div className="pit-team-grid">
          {visible.map((team) => {
            const entry = entriesByTeam.get(team.team_key);
            const done = hasPitEntry(entry);
            const hasPhotos = Boolean(entry?.photos?.length);
            const label = [
              `Team ${team.team_number}`,
              team.nickname,
              done ? 'completed' : null,
              hasPhotos ? 'has photos' : null,
            ].filter(Boolean).join(', ');
            return (
              <button
                key={team.team_key}
                type="button"
                className={[
                  'pit-team-cell',
                  done ? 'pit-team-cell--done' : '',
                  selectedTeam === team.team_key ? 'pit-team-cell--active' : '',
                ].filter(Boolean).join(' ')}
                disabled={disabled}
                onClick={() => {
                  onSelect(team.team_key);
                  setChooserOpen(false);
                }}
                aria-label={label}
                aria-pressed={selectedTeam === team.team_key}
                title={team.nickname ?? undefined}
              >
                <span className="pit-team-cell__head">
                  <strong>{team.team_number}</strong>
                  {done ? <CheckIcon className="pit-team-cell__mark pit-team-cell__mark--done" /> : null}
                  {hasPhotos ? <CameraIcon className="pit-team-cell__mark" /> : null}
                </span>
                {team.nickname ? <span className="pit-team-cell__name">{team.nickname}</span> : null}
              </button>
            );
          })}
        </div>
      )}
    </SurfaceCard>
  );
}
