import { useEffect, useState } from 'react';
import { getScoutingCoverage, type ScoutingCoverageResponse } from '../../api';
import { scoutThanks } from './thanks';
import styles from './ScoutThanks.module.css';

type Props = { eventKey: string; me: string; allDone: boolean };

// A line on My Team's event card for the scout holding the phone: what they've scouted here,
// and a thank-you once their assignments are done. Nothing until their first report.
export function ScoutThanksNote({ eventKey, me, allDone }: Props) {
  const [loaded, setLoaded] = useState<{ eventKey: string; data: ScoutingCoverageResponse } | null>(null);

  useEffect(() => {
    let live = true;
    getScoutingCoverage(eventKey)
      .then((data) => { if (live) setLoaded({ eventKey, data }); })
      .catch(() => undefined);
    return () => { live = false; };
  }, [eventKey]);

  const thanks = loaded?.eventKey === eventKey ? scoutThanks(loaded.data.leaderboard, me, allDone) : null;
  if (!thanks) return null;
  return (
    <p className={`${styles.note} ${allDone ? styles.done : ''}`.trim()}>
      <strong>{thanks.headline}</strong> {thanks.detail}
    </p>
  );
}
