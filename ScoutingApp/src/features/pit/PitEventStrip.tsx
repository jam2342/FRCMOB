import { useState } from 'react';
import type { ReactNode } from 'react';
import { Button } from '../../components/ui/primitives';
import styles from './PitEventStrip.module.css';

// Phones: the intro card and the event field folded into one line (event, progress, Change).
// The picker opens under it on demand, or straight away when no event is chosen yet.
export function PitEventStrip({
  eventName,
  done,
  total,
  picker,
  alert,
  forceOpen,
}: {
  eventName: string;
  done: number;
  total: number;
  picker: (close: () => void) => ReactNode;
  alert?: ReactNode;
  forceOpen: boolean;
}) {
  const [open, setOpen] = useState(false);
  const showPicker = open || forceOpen;
  const name = eventName || 'Choose an event';
  return (
    <section className={styles.strip} data-tour="pit-head" aria-label="Event">
      <div className={styles.line}>
        <strong className={styles.event} title={name}>{name}</strong>
        {total > 0 ? <span className={styles.progress}>{done}/{total} done</span> : null}
        {forceOpen ? null : (
          <Button variant="quiet" size="sm" aria-expanded={open} onClick={() => setOpen((value) => !value)}>
            {open ? 'Close' : 'Change'}
          </Button>
        )}
      </div>
      {showPicker ? picker(() => setOpen(false)) : null}
      {alert}
    </section>
  );
}
