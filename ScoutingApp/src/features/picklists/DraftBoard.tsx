import { useMemo, useState } from 'react';
import type { PicklistSlot } from '../../api';
import { SurfaceCard } from '../../components/ui/SurfaceCard';
import { Button, FieldSelect } from '../../components/ui/primitives';
import { metric } from '../../pages/centerUtils';
import { BestNextPick } from '../picks/BestNextPick';
import { allianceOf, bestAvailable, canBePicked, declineTeam, draftFromSlots, pickTeam, seedCaptains, type Alliance } from './draft';
import styles from './DraftBoard.module.css';

const BEST_AVAILABLE_ROWS = 5;

type TeamInfo = { nickname?: string | null; rating_0_100?: number | null };

type Props = {
  eventKey: string;
  slots: readonly PicklistSlot[];
  // Event rankings, best first; null while loading or unavailable.
  ranked: readonly string[] | null;
  // True when `ranked` comes from the results here rather than TBA's rankings.
  rankedFromResults?: boolean;
  // Every team at the event, for picks our list doesn't hold.
  eventTeams: readonly string[];
  teamInfo: ReadonlyMap<string, TeamInfo>;
  ourTeamKey: string | null;
  // The board's edits, with a sentence for the Undo toast.
  onApply: (next: PicklistSlot[], message: string) => void;
  onOpenTeam: (teamKey: string) => void;
};

function num(teamKey: string): string {
  return teamKey.replace(/^frc/i, '');
}

// The picklist as the selection ceremony runs: eight alliances, who's on the clock, and our best
// available in our order, one tap to record each pick.
export function DraftBoard({ eventKey, slots, ranked, rankedFromResults = false, eventTeams, teamInfo, ourTeamKey, onApply, onOpenTeam }: Props) {
  // Captains who declined an invitation. Kept here, not in the list: a captain's slot already
  // says which alliance they lead.
  const [refused, setRefused] = useState<ReadonlySet<string>>(() => new Set());
  const state = useMemo(() => draftFromSlots(slots, refused), [slots, refused]);
  const [otherTeam, setOtherTeam] = useState('');
  const ourAlliance = allianceOf(state, ourTeamKey);
  const onClock = state.onClock;
  const best = useMemo(() => bestAvailable(slots, state, BEST_AVAILABLE_ROWS), [slots, state]);
  const others = useMemo(() => {
    const keys = new Set([...eventTeams, ...slots.map((slot) => slot.team_key)]);
    return [...keys].filter((key) => canBePicked(state, key)).sort((a, b) => Number(num(a)) - Number(num(b)));
  }, [eventTeams, slots, state]);

  const pick = (teamKey: string) => {
    if (!onClock) return;
    onApply(pickTeam(slots, teamKey, ranked ?? []), `Alliance ${onClock.alliance} picked #${num(teamKey)}`);
    setOtherTeam('');
  };

  const decline = (teamKey: string) => {
    if (state.alliances.some((alliance) => alliance.captain === teamKey)) {
      setRefused((current) => new Set([...current, teamKey]));
      return;
    }
    onApply(declineTeam(slots, teamKey), `#${num(teamKey)} declined`);
  };

  const seed = (message: string) => {
    setRefused(new Set());
    onApply(seedCaptains(slots, ranked ?? []), message);
  };

  if (!state.hasCaptains) {
    return (
      <SurfaceCard tourId="picklist-draft" title="Draft board" subtitle="Follow alliance selection pick by pick." mobileCollapsible={false}>
        <p className={styles.lead}>
          {!ranked?.length
            ? 'The draft board needs the event rankings, and they have not loaded.'
            : rankedFromResults
              ? 'TBA has no rankings for this event yet, so the top eight in the standings from the match results become captains. Check them against the field before you start.'
              : 'The top eight in the rankings become captains of alliances 1 to 8.'}
        </p>
        <Button
          variant="primary"
          disabled={!ranked?.length}
          onClick={() => {
            // A board missing a captain can still hold picks; seeding clears them.
            if (state.picksMade && !window.confirm('Set captains from the rankings? Every pick on the board is cleared.')) return;
            seed('Captains set from the rankings');
          }}
        >
          Set captains from rankings
        </Button>
      </SurfaceCard>
    );
  }

  const subtitle = onClock
    ? `Alliance ${onClock.alliance} is on the clock · round ${onClock.round}`
    : 'Selection complete.';
  const ourOnClock = Boolean(onClock && ourAlliance && ourAlliance.number === onClock.alliance);

  return (
    <SurfaceCard tourId="picklist-draft" title="Draft board" subtitle={subtitle} mobileCollapsible={false}>
      <ol className={styles.grid} aria-label="Alliances">
        {state.alliances.map((alliance) => (
          <AllianceTile
            key={alliance.number}
            alliance={alliance}
            onClock={onClock?.alliance === alliance.number}
            ours={ourAlliance?.number === alliance.number}
          />
        ))}
      </ol>

      {onClock ? (
        <section className={styles.section} aria-label={`Pick for alliance ${onClock.alliance}`}>
          <h4 className={styles.heading}>Best available for alliance {onClock.alliance}</h4>
          {best.length === 0 ? <p className={styles.lead}>Nobody left on our list. Pick from every team below.</p> : null}
          <ul className={styles.best}>
            {best.map((slot) => {
              const info = teamInfo.get(slot.team_key);
              const leads = state.alliances.find((alliance) => alliance.captain === slot.team_key);
              return (
                <li key={slot.team_key} className={styles.bestRow}>
                  <button type="button" className={styles.team} onClick={() => onOpenTeam(slot.team_key)} data-team-peek={slot.team_key} data-team-peek-event={eventKey}>
                    <strong>#{num(slot.team_key)}</strong>
                    {info?.nickname ? <span>{info.nickname}</span> : null}
                    {leads ? <span className={styles.leads}>A{leads.number} captain</span> : null}
                    {info?.rating_0_100 != null ? <span className={styles.rating}>{metric(info.rating_0_100, 0)}</span> : null}
                  </button>
                  <span className={styles.actions}>
                    <Button size="sm" variant="primary" onClick={() => pick(slot.team_key)}>Picked</Button>
                    <Button size="sm" variant="quiet" onClick={() => decline(slot.team_key)}>Declined</Button>
                  </span>
                </li>
              );
            })}
          </ul>
          <form
            className={styles.other}
            onSubmit={(event) => { event.preventDefault(); if (otherTeam) pick(otherTeam); }}
          >
            <FieldSelect label="Someone else" value={otherTeam} onChange={(event) => setOtherTeam(event.target.value)}>
              <option value="">Choose a team</option>
              {others.map((key) => (
                <option key={key} value={key}>#{num(key)}{teamInfo.get(key)?.nickname ? ` · ${teamInfo.get(key)?.nickname}` : ''}</option>
              ))}
            </FieldSelect>
            <Button type="submit" disabled={!otherTeam}>Record pick</Button>
          </form>
        </section>
      ) : null}

      {ourOnClock && ourAlliance?.captain ? (
        <BestNextPick
          eventKey={eventKey}
          partners={[ourAlliance.captain, ...ourAlliance.picks]}
          exclude={state.taken}
          onOpenTeam={onOpenTeam}
        />
      ) : null}

      <Button
        size="sm"
        variant="quiet"
        disabled={!ranked?.length}
        onClick={() => {
          if (window.confirm('Start selection over from the rankings? Every pick on the board is cleared.')) {
            seed('Selection restarted from the rankings');
          }
        }}
      >
        Start over from rankings
      </Button>
    </SurfaceCard>
  );
}

function AllianceTile({ alliance, onClock, ours }: { alliance: Alliance; onClock: boolean; ours: boolean }) {
  const picks = [...alliance.picks, ...Array.from({ length: Math.max(0, 2 - alliance.picks.length) }, () => null)];
  const label = `Alliance ${alliance.number}${onClock ? ', on the clock' : ''}${ours ? ', our alliance' : ''}`;
  return (
    <li className={[styles.tile, onClock ? styles.onClock : '', ours ? styles.ours : ''].filter(Boolean).join(' ')} aria-label={label}>
      <span className={styles.tileHead}>
        A{alliance.number}
        {ours ? <span className={styles.tag}>Us</span> : null}
      </span>
      <span className={styles.captain}>{alliance.captain ? `#${num(alliance.captain)}` : '—'}</span>
      {picks.map((team, i) => (
        <span key={team ?? `empty-${i}`} className={team ? styles.pick : styles.empty}>{team ? `#${num(team)}` : '—'}</span>
      ))}
    </li>
  );
}
