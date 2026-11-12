import type { PitConflict } from '../../api';
import { Button } from '../../components/ui/primitives';
import { fieldLabel, formatAnswer } from './pitMerge';
import styles from './PitConflicts.module.css';

export type PitConflictsProps = {
  conflicts: PitConflict[];
  // What the team has saved now, which may have moved on since the conflict was recorded.
  saved: Record<string, unknown>;
  disabled: boolean;
  onResolve: (field: string, value: unknown) => void;
};

// Two scouts changed the same answer differently. The later save is what's saved; the
// other answer waits here until someone picks.
export function PitConflicts({ conflicts, saved, disabled, onResolve }: PitConflictsProps) {
  if (conflicts.length === 0) return null;
  return (
    <div className="center-callout warning" role="status" aria-label="Answers that differ">
      <p>{conflicts.length === 1 ? 'One answer differs between scouts.' : `${conflicts.length} answers differ between scouts.`} Pick the right one.</p>
      <ul className={styles.list}>
        {conflicts.map((conflict) => {
          const current = saved[conflict.field];
          const who = conflict.other_by?.trim() || 'A teammate';
          return (
            <li key={`${conflict.field}:${conflict.at}`} className={styles.item}>
              <strong>{fieldLabel(conflict.field)}</strong>: saved as {formatAnswer(conflict.field, current)}; {who} said {formatAnswer(conflict.field, conflict.other)}.
              <div className={styles.choices}>
                <Button className={styles.choice} variant="quiet" size="sm" disabled={disabled} onClick={() => onResolve(conflict.field, conflict.other ?? undefined)}>
                  Use {formatAnswer(conflict.field, conflict.other)}
                </Button>
                <Button className={styles.choice} variant="quiet" size="sm" disabled={disabled} onClick={() => onResolve(conflict.field, current)}>
                  Keep {formatAnswer(conflict.field, current)}
                </Button>
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
