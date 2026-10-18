import { useEffect, useRef, useState } from 'react';

// A number that counts up the first time it appears (a team's rating when you open them), then
// eases to new values instead of jumping. Missing values read "N/A" like metric(). Screen readers
// and reduced motion get the final number straight away.

const FIRST_MS = 800;
const UPDATE_MS = 400;

function reducedMotion(): boolean {
  try {
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  } catch {
    return true;
  }
}

function format(value: number, digits: number, prefix: string): string {
  return `${prefix}${value.toFixed(digits)}`;
}

export function CountUp({ value, digits = 0, prefix = '' }: { value: number | null | undefined; digits?: number; prefix?: string }) {
  const target = typeof value === 'number' && Number.isFinite(value) ? value : null;
  const [shown, setShown] = useState<number | null>(() => (target === null || reducedMotion() ? target : 0));
  const fromRef = useRef<number | null>(shown);

  useEffect(() => {
    if (target === null || reducedMotion()) {
      fromRef.current = target;
      setShown(target);
      return undefined;
    }
    const from = fromRef.current ?? 0;
    if (from === target) return undefined;
    const duration = fromRef.current === null || fromRef.current === 0 ? FIRST_MS : UPDATE_MS;
    const startedAt = performance.now();
    let frame = 0;
    const step = (now: number) => {
      const t = Math.min(1, (now - startedAt) / duration);
      const next = from + (target - from) * (1 - (1 - t) ** 3);
      fromRef.current = next;
      setShown(next);
      if (t < 1) frame = requestAnimationFrame(step);
    };
    frame = requestAnimationFrame(step);
    return () => cancelAnimationFrame(frame);
  }, [target]);

  if (target === null) return <>N/A</>;
  // The frame where the number first arrives (before the count starts) shows the starting zero,
  // not the final value: showing it there made the rating flash in full, drop to 0, and count up.
  const display = shown ?? (reducedMotion() ? target : 0);
  return (
    <span style={{ fontVariantNumeric: 'tabular-nums' }} aria-label={format(target, digits, prefix)}>
      <span aria-hidden="true">{format(display, digits, prefix)}</span>
    </span>
  );
}
