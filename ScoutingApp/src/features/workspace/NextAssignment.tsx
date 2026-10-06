import { Link } from 'react-router-dom';
import { scoutLink, type MyAssignment } from './teamAssignments';
import styles from './NextAssignment.module.css';

function timeLabel(startTime: number | null): string {
  if (!startTime) return '';
  const date = new Date(startTime * 1000);
  if (Number.isNaN(date.getTime())) return '';
  const time = date.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  const sameDay = date.toDateString() === new Date().toDateString();
  return sameDay ? `~${time}` : `${date.toLocaleDateString([], { weekday: 'short' })} ~${time}`;
}

function AssignmentLine({ item }: { item: MyAssignment }) {
  const when = timeLabel(item.start_time);
  return (
    <span className={styles.line}>
      <strong className={styles.match}>{item.match_label}</strong>
      <span className={`${styles.alliance} ${item.alliance === 'red' ? styles.red : styles.blue}`}>
        {item.alliance === 'red' ? 'Red' : 'Blue'} {item.team_number}
      </span>
      {when ? <span className={styles.when}>{when}</span> : null}
    </span>
  );
}

// "Your next match" for a scout. onScout keeps the scout on the page they're
// on (Scouting); without it the button links there.
export function NextAssignment({
  eventKey,
  list,
  current,
  onScout,
  emptyText = 'No matches assigned to you yet. Your team lead assigns them on the Assignments page.',
  showAllHref,
}: {
  eventKey: string;
  list: MyAssignment[];
  current?: { match_key: string; team_key: string } | null;
  onScout?: (item: MyAssignment) => void;
  emptyText?: string;
  showAllHref?: string;
}) {
  const remaining = list.filter((item) => !item.done);
  const isCurrent = (item: MyAssignment) =>
    Boolean(current)
    && current!.match_key.toLowerCase() === item.match_key.toLowerCase()
    && current!.team_key.toLowerCase() === item.team_key.toLowerCase();
  // While the scout is on their next slot, offer the one after it.
  const next = remaining.find((item) => !isCurrent(item)) ?? null;
  const scoutingNow = remaining.find(isCurrent) ?? null;
  const later = remaining.filter((item) => item !== next && item !== scoutingNow);

  if (!list.length) {
    return <p className={styles.empty}>{emptyText}</p>;
  }
  if (!remaining.length) {
    return <p className={styles.empty}>You've scouted every match assigned to you here. Nice work.</p>;
  }

  return (
    <div className={styles.wrap}>
      {scoutingNow ? (
        <p className={styles.now}>
          Scouting now: <AssignmentLine item={scoutingNow} />
        </p>
      ) : null}
      {next ? (
        <div className={styles.next}>
          <div className={styles.nextText}>
            <span className={styles.kicker}>{scoutingNow ? 'After this' : 'Your next match'}</span>
            <AssignmentLine item={next} />
          </div>
          {onScout ? (
            <button type="button" className={`center-btn ${styles.scoutButton}`} onClick={() => onScout(next)}>
              Scout team {next.team_number}
            </button>
          ) : (
            <Link className={`center-btn ${styles.scoutButton}`} to={scoutLink(eventKey, next)}>
              Scout team {next.team_number}
            </Link>
          )}
        </div>
      ) : null}
      {later.length ? (
        <details className={styles.later}>
          <summary>
            {later.length} more assigned to you
          </summary>
          <ul className={styles.laterList}>
            {later.map((item) => (
              <li key={`${item.match_key}:${item.team_key}`}>
                {onScout ? (
                  <button type="button" className={styles.laterButton} onClick={() => onScout(item)}>
                    <AssignmentLine item={item} />
                  </button>
                ) : (
                  <Link className={styles.laterButton} to={scoutLink(eventKey, item)}>
                    <AssignmentLine item={item} />
                  </Link>
                )}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
      {showAllHref ? (
        <Link className={styles.allLink} to={showAllHref}>See all your assignments</Link>
      ) : null}
    </div>
  );
}
