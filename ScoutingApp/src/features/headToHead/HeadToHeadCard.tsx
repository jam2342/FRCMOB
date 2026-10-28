import { useEffect, useMemo, useState } from 'react';
import { getTeamHeadToHead, type HeadToHeadRecord, type HeadToHeadResponse } from '../../api';
import { EmptyState } from '../../components/ui/EmptyState';
import { SkeletonBlock } from '../../components/ui/SkeletonBlock';
import { SurfaceCard } from '../../components/ui/SurfaceCard';
import { Button, FieldRadioGroup } from '../../components/ui/primitives';
import { teamNumberFromTeamKey } from '../../pages/centerUtils';
import { matchLabelFromKey } from '../../utils/matchLabel';
import { recordLine } from './headToHead';
import styles from './HeadToHead.module.css';

const FIRST_PAGE = 6;

type Props = { teamKeys: readonly string[] };

function num(teamKey: string): string {
  return `#${teamNumberFromTeamKey(teamKey) ?? teamKey}`;
}

// How two teams have done with and against each other, every season on record. With three
// teams in Compare, a row of pairs picks which two.
export function HeadToHeadCard({ teamKeys }: Props) {
  const pairs = useMemo(() => {
    const out: Array<[string, string]> = [];
    for (let i = 0; i < teamKeys.length; i += 1) {
      for (let j = i + 1; j < teamKeys.length; j += 1) out.push([teamKeys[i], teamKeys[j]]);
    }
    return out.slice(0, 6);
  }, [teamKeys]);
  const [pairIndex, setPairIndex] = useState(0);
  const pair = pairs[Math.min(pairIndex, pairs.length - 1)];
  const scope = pair ? pair.join('|') : '';
  const [loaded, setLoaded] = useState<{ scope: string; data: HeadToHeadResponse } | null>(null);
  const [failed, setFailed] = useState('');
  const [showAll, setShowAll] = useState(false);

  useEffect(() => {
    if (!pair) return undefined;
    let live = true;
    setFailed('');
    setShowAll(false);
    getTeamHeadToHead(pair[0], pair[1])
      .then((data) => { if (live) setLoaded({ scope, data }); })
      .catch(() => { if (live) setFailed(scope); });
    return () => { live = false; };
    // `scope` stands for the pair.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scope]);

  if (!pair) return null;
  const data = loaded?.scope === scope ? loaded.data : null;
  const [a, b] = pair;
  const visible = data ? (showAll ? data.matches : data.matches.slice(0, FIRST_PAGE)) : [];

  return (
    <SurfaceCard title="Head to head" subtitle="Every match on record with and against each other." compactable>
      {pairs.length > 1 ? (
        <FieldRadioGroup
          legend="Which two"
          name="head-to-head-pair"
          segmented
          value={String(Math.min(pairIndex, pairs.length - 1))}
          onValueChange={(value) => setPairIndex(Number(value))}
          options={pairs.map(([x, y], i) => ({ value: String(i), label: `${num(x)} & ${num(y)}` }))}
          className={styles.pairs}
        />
      ) : null}
      {!data && failed !== scope ? <SkeletonBlock rows={3} compact /> : null}
      {failed === scope ? <EmptyState compact type="offline" title="Couldn't load their history" description="Try again in a moment." /> : null}
      {data && data.match_count === 0 ? (
        <p className="center-callout muted">{num(a)} and {num(b)} have never played in the same match.</p>
      ) : null}
      {data && data.match_count > 0 ? (
        <>
          <div className={styles.records}>
            <Record title={`${num(a)} with ${num(b)}`} record={data.together} />
            <Record title={`${num(a)} against ${num(b)}`} record={data.against} />
          </div>
          <ul className={styles.matches}>
            {visible.map((match) => (
              <li key={match.match_key} className={styles.match}>
                <span className={styles.where}>
                  <strong>{matchLabelFromKey(match.match_key) ?? match.match_key}</strong>
                  <span> · {match.event_name || match.event_key}</span>
                </span>
                <span className={styles.what}>
                  <span className={styles.relation}>{match.relation === 'together' ? 'Together' : 'Against'}</span>
                  {match.score != null && match.opponent_score != null ? (
                    <span className={match.alliance === 'red' ? styles.red : styles.blue}>{match.score}–{match.opponent_score}</span>
                  ) : null}
                  <span className={match.result === 'win' ? styles.win : match.result === 'loss' ? styles.loss : styles.even}>
                    {match.result === 'win' ? `${num(a)} won` : match.result === 'loss' ? `${num(a)} lost` : match.result === 'tie' ? 'Tie' : 'Not played'}
                  </span>
                </span>
              </li>
            ))}
          </ul>
          {data.matches.length > FIRST_PAGE ? (
            <Button size="sm" variant="quiet" onClick={() => setShowAll((value) => !value)}>
              {showAll ? 'Show fewer' : `Show all ${data.matches.length}`}
            </Button>
          ) : null}
        </>
      ) : null}
    </SurfaceCard>
  );
}

function Record({ title, record }: { title: string; record: HeadToHeadRecord }) {
  return (
    <div className={styles.record}>
      <span className={styles.recordTitle}>{title}</span>
      <strong>{record.played ? `${record.wins}–${record.losses}${record.ties ? `–${record.ties}` : ''}` : '—'}</strong>
      <span className={styles.recordSub}>{recordLine(record)}</span>
    </div>
  );
}
