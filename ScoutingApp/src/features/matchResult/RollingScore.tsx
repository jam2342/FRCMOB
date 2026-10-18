import { useEffect, useState } from 'react';
import styles from './MatchResult.module.css';

// "443 - 665", counted up from zero when a result has just arrived on screen. Otherwise it is
// plain text. Screen readers get the final score straight away.

const ROLL_MS = 1100;

function reducedMotion(): boolean {
  try {
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  } catch {
    return false;
  }
}

export function RollingScore({ red, blue, roll }: { red: number; blue: number; roll: boolean }) {
  const [shown, setShown] = useState<[number, number]>(() => (roll && !reducedMotion() ? [0, 0] : [red, blue]));

  useEffect(() => {
    if (!roll || reducedMotion()) {
      setShown([red, blue]);
      return undefined;
    }
    const startedAt = performance.now();
    let frame = 0;
    const step = (now: number) => {
      const t = Math.min(1, (now - startedAt) / ROLL_MS);
      const eased = 1 - (1 - t) ** 3;
      setShown([Math.round(red * eased), Math.round(blue * eased)]);
      if (t < 1) frame = requestAnimationFrame(step);
    };
    frame = requestAnimationFrame(step);
    return () => cancelAnimationFrame(frame);
  }, [red, blue, roll]);

  return (
    <>
      <span aria-hidden="true">{`${shown[0]} - ${shown[1]}`}</span>
      <span className={styles.srOnly}>{`${red} - ${blue}`}</span>
    </>
  );
}
