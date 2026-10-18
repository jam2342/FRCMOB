import { MATCH_PHASES, SEASON } from '../../config/season';
import styles from './MatchClock.module.css';

// The match timer drawn as REBUILT's phases (Auto, Transition, Shifts 1-4, Endgame) instead of one
// bar, with the current phase named. It's an estimate from the start time, like the bar it replaces.

function matchPhaseAt(elapsedSec: number) {
  return MATCH_PHASES.find((phase) => elapsedSec >= phase.startSec && elapsedSec < phase.endSec) ?? null;
}

export function MatchClock({ progress, className }: { progress: number; className?: string }) {
  const elapsed = Math.max(0, Math.min(SEASON.matchSec, (progress / 100) * SEASON.matchSec));
  // Exactly 0 is "not started": the timer only counts once the start time has passed.
  const current = elapsed > 0 ? matchPhaseAt(elapsed) : null;
  const phaseLeft = current ? Math.ceil(current.endSec - elapsed) : 0;
  const text = current ? `${current.label}, ${phaseLeft}s left in it` : elapsed >= SEASON.matchSec ? 'Match time over' : 'Not started';
  return (
    <div className={`${styles.clock} ${className ?? ''}`}>
      <div
        className={styles.track}
        role="progressbar"
        aria-label="Match clock"
        aria-valuemin={0}
        aria-valuemax={SEASON.matchSec}
        aria-valuenow={Math.round(elapsed)}
        aria-valuetext={text}
      >
        {MATCH_PHASES.map((phase) => {
          const span = phase.endSec - phase.startSec;
          const filled = Math.max(0, Math.min(1, (elapsed - phase.startSec) / span));
          return (
            <span
              key={phase.key}
              className={`${styles.segment} ${current?.key === phase.key ? styles.current : ''}`}
              style={{ flexGrow: span }}
            >
              <span className={styles.fill} style={{ transform: `scaleX(${filled})` }} />
            </span>
          );
        })}
      </div>
      <div className={styles.labels} aria-hidden="true">
        <span className={styles.now}>{current ? current.label : elapsed >= SEASON.matchSec ? 'Time' : 'Not started yet'}</span>
        {current ? <span>{phaseLeft}s left</span> : null}
      </div>
    </div>
  );
}
