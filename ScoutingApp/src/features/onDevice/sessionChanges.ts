let batchDepth = 0;
let changed = false;

export function notifySessionChange(): void {
  if (batchDepth) {
    changed = true;
    return;
  }
  if (typeof window !== 'undefined') window.dispatchEvent(new Event('frcmob:session-change'));
}

export function beginSessionChangeBatch(): () => void {
  batchDepth++;
  return () => {
    batchDepth--;
    if (!batchDepth && changed) {
      changed = false;
      notifySessionChange();
    }
  };
}

export function coalesceRefresh(refresh: () => Promise<void>) {
  let timer: number | undefined;
  let inFlight: Promise<void> | null = null;
  let pending = false;
  let disposed = false;
  const flush = async () => {
    if (disposed) return;
    window.clearTimeout(timer);
    timer = undefined;
    if (inFlight) {
      pending = true;
      await inFlight;
      return;
    }
    pending = false;
    inFlight = Promise.resolve().then(refresh);
    try {
      await inFlight;
    } finally {
      inFlight = null;
      if (pending) schedule();
    }
  };
  const schedule = () => {
    if (disposed) return;
    pending = true;
    if (inFlight || timer !== undefined) return;
    timer = window.setTimeout(() => {
      void flush();
    }, 0);
  };
  return {
    schedule,
    flush,
    cancel: () => {
      disposed = true;
      window.clearTimeout(timer);
    },
  };
}
