import { useEffect, useMemo, useState } from 'react';
import { getEventMatchup, type EventMatchupResponse } from '../../api';
import { SurfaceCard } from '../../components/ui/SurfaceCard';
import { Button, FieldSelect, FieldToggle } from '../../components/ui/primitives';
import { teamNumberFromTeamKey } from '../../pages/centerUtils';
import styles from './CustomMatchup.module.css';

type Side = 'red' | 'blue';
type Lineup = Record<Side, [string, string, string]>;

const EMPTY: Lineup = { red: ['', '', ''], blue: ['', '', ''] };

const UNAVAILABLE: Record<NonNullable<EventMatchupResponse['unavailable']>, string> = {
  season_not_rated: 'FRCMOB ratings start in 2026, so earlier seasons have no predictions.',
  season_frozen: 'This season is finished and kept as it ended; match-ups are for the current season.',
  no_prediction: 'The model has nothing on these teams yet.',
};

type Props = { eventKey: string; teamKeys: readonly string[] };

function label(teamKey: string): string {
  return `#${teamNumberFromTeamKey(teamKey) ?? teamKey}`;
}

// A match that isn't on the schedule: pick any six teams here and see how it would go now.
// Same model as the schedule's predictions, so the two never disagree about a team.
export function CustomMatchup({ eventKey, teamKeys }: Props) {
  const [lineup, setLineup] = useState<Lineup>(EMPTY);
  const [playoff, setPlayoff] = useState(false);
  const [result, setResult] = useState<{ query: string; data: EventMatchupResponse } | null>(null);
  const [failed, setFailed] = useState('');

  useEffect(() => { setLineup(EMPTY); setResult(null); setFailed(''); }, [eventKey]);

  const sorted = useMemo(
    () => [...teamKeys].sort((a, b) => (teamNumberFromTeamKey(a) ?? 0) - (teamNumberFromTeamKey(b) ?? 0)),
    [teamKeys],
  );
  const chosen = [...lineup.red, ...lineup.blue];
  const complete = chosen.every(Boolean);
  const query = complete ? `${eventKey}|${lineup.red.join(',')}|${lineup.blue.join(',')}|${playoff}` : '';

  useEffect(() => {
    if (!query) return undefined;
    let live = true;
    setFailed('');
    getEventMatchup(eventKey, lineup.red, lineup.blue, playoff)
      .then((data) => { if (live) setResult({ query, data }); })
      .catch((err: Error) => { if (live) setFailed(err.message || 'Could not predict this match-up.'); });
    return () => { live = false; };
    // `query` stands for the lineup and the playoff switch.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query]);

  const data = result?.query === query ? result.data : null;
  const set = (side: Side, index: number, teamKey: string) => {
    setLineup((current) => {
      const next: Lineup = { red: [...current.red], blue: [...current.blue] };
      next[side][index] = teamKey;
      return next;
    });
  };

  return (
    <SurfaceCard tourId="predictions-custom" title="Custom match-up" subtitle="Any six teams here, as if they played now." compactable>
      <div className={styles.sides}>
        {(['red', 'blue'] as const).map((side) => (
          <fieldset key={side} className={`${styles.side} ${styles[side]}`}>
            <legend className={styles.legend}>{side === 'red' ? 'Red alliance' : 'Blue alliance'}</legend>
            {lineup[side].map((teamKey, index) => (
              <FieldSelect
                key={index}
                label={`${side === 'red' ? 'Red' : 'Blue'} ${index + 1}`}
                value={teamKey}
                onChange={(event) => set(side, index, event.target.value)}
              >
                <option value="">Choose a team</option>
                {sorted
                  .filter((key) => key === teamKey || !chosen.includes(key))
                  .map((key) => <option key={key} value={key}>{label(key)}</option>)}
              </FieldSelect>
            ))}
          </fieldset>
        ))}
      </div>
      <div className={styles.controls}>
        <FieldToggle label="Playoff match" checked={playoff} onCheckedChange={setPlayoff} />
        <Button size="sm" variant="quiet" onClick={() => setLineup((current) => ({ red: current.blue, blue: current.red }))}>
          Swap sides
        </Button>
      </div>

      {!complete ? <p className={styles.note}>Choose all six teams to see the prediction.</p> : null}
      {failed ? <p className="center-callout warning" role="alert">{failed}</p> : null}
      {data?.unavailable ? <p className={styles.note}>{UNAVAILABLE[data.unavailable]}</p> : null}
      {data && !data.unavailable && data.red_win_prob != null && data.blue_win_prob != null ? (
        <MatchupResult data={data} />
      ) : null}
    </SurfaceCard>
  );
}

function MatchupResult({ data }: { data: EventMatchupResponse }) {
  const red = Math.round((data.red_win_prob ?? 0.5) * 100);
  const blue = 100 - red;
  const even = Math.abs(red - 50) <= 2;
  const verdict = even ? 'About even' : red > blue ? `Red favoured · ${red}% to win` : `Blue favoured · ${blue}% to win`;
  return (
    <div className={styles.result} aria-live="polite">
      <div className={styles.scores}>
        <span className={styles.redText}><strong>{Math.round(data.red_points ?? 0)}</strong> expected</span>
        <span className={styles.vs} aria-hidden="true">vs</span>
        <span className={styles.blueText}><strong>{Math.round(data.blue_points ?? 0)}</strong> expected</span>
      </div>
      <div className={styles.bar} role="img" aria-label={`Red ${red}%, blue ${blue}%`}>
        <span className={styles.barRed} style={{ width: `${red}%` }} />
        <span className={styles.barBlue} style={{ width: `${blue}%` }} />
      </div>
      <p className={styles.verdict}>{verdict}</p>
      {(data.matches_seen ?? 3) < 3 ? (
        <p className={styles.note}>Some of these teams have played fewer than three matches here, so this is less certain.</p>
      ) : null}
    </div>
  );
}
