import { useEffect } from 'react';
import { hapticSuccess } from '../../utils/haptics';
import styles from './MatchResult.module.css';

// The result landing on a win for your own team: a line that pops in after the score has rolled,
// a few sparks, and one success buzz. Shown only during the reveal, never for a match opened
// already finished. Reduced motion keeps the line and drops the movement.
export function OurWin({ teamNumber }: { teamNumber: number }) {
  useEffect(() => {
    const timer = window.setTimeout(hapticSuccess, 1100);
    return () => window.clearTimeout(timer);
  }, []);
  return (
    <span className={styles.ourWin} role="status">
      <span className={styles.sparks} aria-hidden="true" />
      Team {teamNumber} wins!
    </span>
  );
}
