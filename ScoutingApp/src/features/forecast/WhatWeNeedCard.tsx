import { useEffect, useMemo, useState } from 'react';
import { getEventWhatIf, type EventWhatIfResponse, type WhatIfRank } from '../../api';
import { SurfaceCard } from '../../components/ui/SurfaceCard';
import { Button, FieldRadioGroup } from '../../components/ui/primitives';
import { pct, teamNumberFromTeamKey } from '../../pages/centerUtils';
import { matchLabelFromKey } from '../../utils/matchLabel';
import { whatIfLists, type Choice } from './whatIf';
import styles from './WhatWeNeed.module.css';

type Props = { eventKey: string; teamKey: string };

// A burst of taps (Win all, then one back to the model) asks once.
const ASK_AFTER_MS = 250;

function num(teamKey: string): string {
  return String(teamNumberFromTeamKey(teamKey) ?? teamKey);
}

function rankLine(rank: WhatIfRank): string {
  const range = rank.rank_likely_best === rank.rank_likely_worst
    ? `${rank.rank_likely_best}`
    : `${rank.rank_likely_best}–${rank.rank_likely_worst}`;
  return `Likely rank ${range} · ${pct(rank.top8, 0)} top 8`;
}

// "What do we need?": set the rest of a team's quals to a win or a loss and see where it
// would finish, next to where the model has it now. Drawn only while the team has quals left.
export function WhatWeNeedCard({ eventKey, teamKey }: Props) {
  const scope = `${eventKey}|${teamKey}`;
  const [choices, setChoices] = useState<Record<string, Choice>>({});
  const [loaded, setLoaded] = useState<{ scope: string; query: string; data: EventWhatIfResponse } | null>(null);
  const [failed, setFailed] = useState(false);
  const lists = useMemo(() => whatIfLists(choices), [choices]);
  const query = `${scope}|${lists.wins.join(',')}|${lists.losses.join(',')}`;

  useEffect(() => { setChoices({}); }, [scope]);

  useEffect(() => {
    let live = true;
    const timer = window.setTimeout(() => {
      getEventWhatIf(eventKey, teamKey, lists.wins, lists.losses)
        .then((data) => { if (live) { setLoaded({ scope, query, data }); setFailed(false); } })
        .catch(() => { if (live) setFailed(true); });
    }, ASK_AFTER_MS);
    return () => { live = false; window.clearTimeout(timer); };
    // `query` stands for the event, the team and both lists.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query]);

  // The last answer for this team stays up while a new one is on its way.
  const data = loaded?.scope === scope ? loaded.data : null;
  if (!data || data.unavailable || !data.matches.length || !data.model || !data.what_if) {
    return failed && data ? <SurfaceCard title="What do we need?"><p className="center-callout warning">Couldn't run the what-if. It tries again on your next change.</p></SurfaceCard> : null;
  }

  const choose = (matchKey: string, choice: Choice) => {
    setChoices((current) => {
      const next = { ...current };
      if (choice === 'model') delete next[matchKey];
      else next[matchKey] = choice;
      return next;
    });
  };
  const setAll = (choice: Choice) => {
    setChoices(choice === 'model' ? {} : Object.fromEntries(data.matches.map((row) => [row.match_key, choice])));
  };
  const changed = Object.keys(choices).length > 0;
  const stale = loaded?.query !== query;

  return (
    <SurfaceCard
      title="What do we need?"
      subtitle={`Set the rest of #${num(teamKey)}'s quals and see where they'd finish.`}
      compactable
    >
      <div className={styles.summary} aria-live="polite" aria-busy={stale || undefined}>
        <div className={styles.figure}>
          <span className={styles.label}>Model now</span>
          <strong>{data.model.expected_rank.toFixed(1)}</strong>
          <span className={styles.sub}>{rankLine(data.model)}</span>
        </div>
        <div className={`${styles.figure} ${changed ? styles.figureActive : ''}`.trim()}>
          <span className={styles.label}>With your picks</span>
          <strong>{changed ? data.what_if.expected_rank.toFixed(1) : '—'}</strong>
          <span className={styles.sub}>{changed ? rankLine(data.what_if) : 'Set a match below'}</span>
        </div>
      </div>
      {stale && failed ? (
        // Otherwise the figure above reads as the answer for the picks now on screen.
        <p className="center-callout warning" role="status">Couldn't update this for your latest picks. It tries again on your next change.</p>
      ) : null}
      <p className={styles.note}>
        Average rank out of {data.field_size} teams{data.runs ? `, from ${data.runs.toLocaleString()} runs of the remaining quals` : ''}.
      </p>

      <div className={styles.presets}>
        <Button size="sm" onClick={() => setAll('win')}>Win them all</Button>
        <Button size="sm" onClick={() => setAll('loss')}>Lose them all</Button>
        <Button size="sm" variant="quiet" disabled={!changed} onClick={() => setAll('model')}>Back to the model</Button>
      </div>

      <ul className={styles.matches}>
        {data.matches.map((row) => (
          <li key={row.match_key} className={styles.match}>
            <span className={styles.matchHead}>
              <strong className={row.alliance === 'red' ? styles.red : styles.blue}>{matchLabelFromKey(row.match_key) ?? row.match_key}</strong>
              <span className={styles.lineup}>with {row.partners.map(num).join(', ')} · vs {row.opponents.map(num).join(', ')}</span>
            </span>
            <FieldRadioGroup
              legend={`Result of ${matchLabelFromKey(row.match_key) ?? row.match_key}`}
              name={`what-if-${row.match_key}`}
              segmented
              value={choices[row.match_key] ?? 'model'}
              onValueChange={(value) => choose(row.match_key, value as Choice)}
              options={[
                { value: 'win', label: 'Win' },
                { value: 'model', label: `Model ${pct(row.model_win_prob, 0)}` },
                { value: 'loss', label: 'Loss' },
              ]}
              className={styles.choice}
            />
          </li>
        ))}
      </ul>
    </SurfaceCard>
  );
}
