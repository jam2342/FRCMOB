import { useEffect, useState } from 'react';

export function usePageClock(visible: boolean, intervalMs = 1000): number {
  const [nowMs, setNowMs] = useState(() => Date.now());

  useEffect(() => {
    if (!visible) return;
    const timer = window.setInterval(() => setNowMs(Date.now()), Math.max(250, Math.floor(intervalMs)));
    return () => window.clearInterval(timer);
  }, [intervalMs, visible]);

  return nowMs;
}
