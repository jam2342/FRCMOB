import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { getScoutingCoverage, type WorkspaceMemberRecord } from '../../api';
import { SurfaceCard } from '../../components/ui/SurfaceCard';
import { Stat } from '../../components/ui/primitives';
import styles from './LeaderDashboard.module.css';

const ACTIVE_WITHIN_MS = 10 * 60_000;
const DASH = '—';

type Props = {
  eventKey: string;
  members: WorkspaceMemberRecord[] | undefined;
  // Null while the schedule or room hasn't loaded (or failed).
  slots: { open: number; needNewScout: number } | null;
  pit: { done: number; total: number } | null;
};

function activeScouts(members: WorkspaceMemberRecord[], now = Date.now()): number {
  return members.filter((member) => {
    const seen = member.last_seen_at ? new Date(member.last_seen_at).getTime() : NaN;
    return Number.isFinite(seen) && now - seen <= ACTIVE_WITHIN_MS;
  }).length;
}

function useCoveragePct(eventKey: string): number | null {
  const [result, setResult] = useState<{ eventKey: string; pct: number } | null>(null);
  useEffect(() => {
    let cancelled = false;
    getScoutingCoverage(eventKey)
      .then((response) => {
        const pct = Number(response?.summary?.coverage_pct);
        if (!cancelled) setResult(Number.isFinite(pct) ? { eventKey, pct } : null);
      })
      .catch(() => { if (!cancelled) setResult(null); });
    return () => { cancelled = true; };
  }, [eventKey]);
  return result && result.eventKey === eventKey ? result.pct : null;
}

// Leaders' top-of-page numbers. Each one leads to the place you act on it.
export function LeaderDashboard({ eventKey, members, slots, pit }: Props) {
  const coverage = useCoveragePct(eventKey);
  const query = `?event=${encodeURIComponent(eventKey)}`;
  const active = members ? activeScouts(members) : null;

  return (
    <SurfaceCard tourId="myteam-dashboard" title="Right now" mobileCollapsible={false}>
      <div className={styles.grid}>
        <button
          type="button"
          className={styles.cell}
          onClick={() => document.querySelector('[data-tour="myteam-members"]')?.scrollIntoView({ block: 'start', behavior: 'smooth' })}
        >
          <Stat
            size="sm"
            label="Scouts active"
            value={active === null || !members ? DASH : `${active}/${members.length}`}
            sub="seen in 10 min"
          />
        </button>
        <Link className={styles.cell} to={`/scouting/assignments${query}`}>
          <Stat
            size="sm"
            label="Open slots"
            value={slots ? slots.open : DASH}
            sub={slots && slots.needNewScout > 0 ? `${slots.needNewScout} need a new scout` : 'upcoming matches'}
            tone={slots && slots.needNewScout > 0 ? 'warning' : 'default'}
          />
        </Link>
        <Link className={styles.cell} to={`/scouting/coverage${query}`}>
          <Stat
            size="sm"
            label="Match coverage"
            value={coverage === null ? DASH : `${Math.round(coverage)}%`}
            sub="slots scouted"
          />
        </Link>
        <Link className={styles.cell} to={`/scouting/pit${query}`}>
          <Stat
            size="sm"
            label="Pit scouting"
            value={pit ? `${pit.done}/${pit.total}` : DASH}
            sub="teams done"
          />
        </Link>
      </div>
    </SurfaceCard>
  );
}
