import { useCallback, useEffect, useRef, useState } from 'react';

export function useScoutCountdown(duration: number, visible: boolean) {
  const [seconds, setSeconds] = useState(duration);
  const [running, setRunning] = useState(false);
  const deadline = useRef<number | null>(null);
  const remainingMs = useRef(duration * 1000);

  const update = useCallback(() => {
    if (deadline.current === null) return;
    remainingMs.current = Math.max(0, deadline.current - Date.now());
    setSeconds(Math.ceil(remainingMs.current / 1000));
    if (!remainingMs.current) {
      deadline.current = null;
      setRunning(false);
    }
  }, []);

  const setCountdown = useCallback((next: number, start = false) => {
    remainingMs.current = next * 1000;
    deadline.current = start && next > 0 ? Date.now() + remainingMs.current : null;
    setSeconds(next);
    setRunning(deadline.current !== null);
  }, []);

  const toggle = useCallback(() => {
    if (deadline.current !== null) {
      update();
      deadline.current = null;
      setRunning(false);
    } else if (remainingMs.current > 0) {
      deadline.current = Date.now() + remainingMs.current;
      setRunning(true);
    }
  }, [update]);

  useEffect(() => {
    if (!running || !visible) return;
    update();
    const timer = window.setInterval(update, 250);
    return () => window.clearInterval(timer);
  }, [running, visible, update]);

  return { seconds, running, setCountdown, toggle };
}
