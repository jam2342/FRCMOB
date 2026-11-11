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

  const scouts = active === null || !members ? DASH : `${active}/${members.length}`;
  const open = slots ? String(slots.open) : DASH;
  const cov = coverage === null ? DASH : `${Math.round(coverage)}%`;
  const pitValue = pit ? `${pit.done}/${pit.total}` : DASH;
  const chevron = <span className={styles.chevron} aria-hidden="true">›</span>;

  return (
    <SurfaceCard tourId="myteam-dashboard" title="Right now" mobileCollapsible={false}>
      <div className={styles.grid}>
        <button
          type="button"
          className={styles.cell}
          aria-label={`Scouts active ${scouts}, see members`}
          onClick={() => document.querySelector('[data-tour="myteam-members"]')?.scrollIntoView({ block: 'start', behavior: 'smooth' })}
        >
          <Stat size="sm" label="Scouts active" value={scouts} sub="seen in 10 min" />
          {chevron}
        </button>
        <Link className={styles.cell} to={`/scouting/assignments${query}`} aria-label={`Open slots ${open}, open Assignments`}>
          <Stat
            size="sm"
            label="Open slots"
            value={open}
            sub={slots && slots.needNewScout > 0 ? `${slots.needNewScout} need a new scout` : 'upcoming matches'}
            tone={slots && slots.needNewScout > 0 ? 'warning' : 'default'}
          />
          {chevron}
        </Link>
        <Link className={styles.cell} to={`/scouting/coverage${query}`} aria-label={`Match coverage ${cov}, open Coverage`}>
          <Stat size="sm" label="Match coverage" value={cov} sub="slots scouted" />
          {chevron}
        </Link>
        <Link className={styles.cell} to={`/scouting/pit${query}`} aria-label={`Pit scouting ${pitValue}, open Pit scouting`}>
          <Stat size="sm" label="Pit scouting" value={pitValue} sub="teams done" />
          {chevron}
        </Link>
      </div>
    </SurfaceCard>
  );
}
