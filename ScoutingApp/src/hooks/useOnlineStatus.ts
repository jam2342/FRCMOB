import { resolveApiBaseUrl } from '../platform/runtime';
import { useSyncExternalStore } from 'react';
import { size as offlineQueueSize } from '../utils/offlineQueue';

const HEALTH_URL = resolveApiBaseUrl() + '/health';
const POLL_INTERVAL_MS = 30_000;
const FETCH_TIMEOUT_MS = 5_000;

// "answered" means the phone reached a server: an error status is the server having
// trouble, not the phone being offline, and the banner says which.
async function _pingBackend(): Promise<{ reachable: boolean; answered: boolean }> {
  const controller = new AbortController();
  const id = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
  try {
    const res = await fetch(HEALTH_URL, { method: 'GET', signal: controller.signal });
    return { reachable: res.ok, answered: true };
  } catch {
    return { reachable: false, answered: false };
  } finally {
    clearTimeout(id);
  }
}

type OnlineStatusSnapshot = {
  online: boolean;
  // Not online because the server answered with an error (it's down or overloaded).
  serverTrouble: boolean;
  queueSize: number;
  isShowingOfflineData: boolean;
};

const subscribers = new Set<() => void>();
let snapshot: OnlineStatusSnapshot = {
  online: typeof navigator !== 'undefined' ? navigator.onLine : true,
  serverTrouble: false,
  queueSize: typeof window !== 'undefined' ? offlineQueueSize() : 0,
  isShowingOfflineData: false,
};
let intervalId: ReturnType<typeof setInterval> | null = null;
let initialCheckId: ReturnType<typeof setTimeout> | null = null;
let checkSequence = 0;
let monitoring = false;

function updateSnapshot(next: Partial<OnlineStatusSnapshot>) {
  const updated = { ...snapshot, ...next };
  if (
    updated.online === snapshot.online &&
    updated.queueSize === snapshot.queueSize &&
    updated.isShowingOfflineData === snapshot.isShowingOfflineData
  ) {
    return;
  }
  snapshot = updated;
  subscribers.forEach((subscriber) => subscriber());
}

async function runCheck() {
  const sequence = ++checkSequence;
  if (typeof navigator !== 'undefined' && !navigator.onLine) {
    updateSnapshot({ online: false });
    return;
  }
  const { reachable, answered } = await _pingBackend();
  if (!monitoring || sequence !== checkSequence) return;
  if (typeof navigator !== 'undefined' && !navigator.onLine) {
    updateSnapshot({ online: false, serverTrouble: false });
    return;
  }
  updateSnapshot({
    online: reachable,
    serverTrouble: !reachable && answered,
    // A confirmed live backend replaces any stale cached response.
    isShowingOfflineData: reachable ? false : snapshot.isShowingOfflineData,
  });
}

const onOffline = () => {
  checkSequence += 1;
  updateSnapshot({ online: false, serverTrouble: false });
};
const onOnline = () => { void runCheck(); };
const onQueueChange = (e: Event) => {
  const detail = (e as CustomEvent<{ count: number }>).detail;
  updateSnapshot({ queueSize: detail?.count ?? 0 });
};
const onOfflineStale = () => updateSnapshot({ isShowingOfflineData: true });

function startMonitoring() {
  if (monitoring || typeof window === 'undefined') return;
  monitoring = true;
  updateSnapshot({
    online: typeof navigator !== 'undefined' ? navigator.onLine : true,
    queueSize: offlineQueueSize(),
  });
  initialCheckId = setTimeout(() => {
    initialCheckId = null;
    void runCheck();
  }, 0);
  window.addEventListener('offline', onOffline);
  window.addEventListener('online', onOnline);
  window.addEventListener('offlinequeue:change', onQueueChange);
  window.addEventListener('api:offline-stale', onOfflineStale);
  intervalId = setInterval(() => { void runCheck(); }, POLL_INTERVAL_MS);
}

function stopMonitoring() {
  if (!monitoring || subscribers.size > 0 || typeof window === 'undefined') return;
  monitoring = false;
  checkSequence += 1;
  if (initialCheckId !== null) clearTimeout(initialCheckId);
  if (intervalId !== null) clearInterval(intervalId);
  initialCheckId = null;
  intervalId = null;
  window.removeEventListener('offline', onOffline);
  window.removeEventListener('online', onOnline);
  window.removeEventListener('offlinequeue:change', onQueueChange);
  window.removeEventListener('api:offline-stale', onOfflineStale);
}

function subscribe(subscriber: () => void) {
  subscribers.add(subscriber);
  startMonitoring();
  return () => {
    subscribers.delete(subscriber);
    stopMonitoring();
  };
}

const getSnapshot = () => snapshot;
const getServerSnapshot = () => snapshot;

export function useOnlineStatus() {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}
