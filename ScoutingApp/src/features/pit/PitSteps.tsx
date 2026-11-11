import { PIT_STEPS } from './pitStepConfig';
import styles from './PitForm.module.css';

// The step you're on says where you are ("1 of 3 · Robot"); the others shrink to a number
// you can tap, so the strip fits a 250px phone.
export function PitSteps({ index, onSelect }: { index: number; onSelect: (next: number) => void }) {
  return (
    <nav className={styles.steps} aria-label="Form steps">
      {PIT_STEPS.map((step, position) => {
        const current = position === index;
        const text = `${position + 1} of ${PIT_STEPS.length} · ${step.label}`;
        return (
          <button
            key={step.id}
            type="button"
            className={`${styles.step} ${current ? styles.stepCurrent : ''}`}
            aria-current={current ? 'step' : undefined}
            aria-label={current ? text : `Go to step ${position + 1}, ${step.label}`}
            title={text}
            onClick={() => onSelect(position)}
          >
            <span className={styles.stepText}>{current ? text : position + 1}</span>
          </button>
        );
      })}
    </nav>
  );
}
