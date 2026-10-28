// One socket per in-play event a page is showing, saying when its results change
// (backend: services/events/live_signal.py). A change refreshes every poll on screen
// at once instead of after its interval and the edge cache's lifetime.
import { liveResultsWebSocketUrl } from '../../api';
import { applyLiveVersion, setLiveInterestListener } from './liveVersions';

const MAX_SOCKETS = 6;
// Polls ask at least every minute while a page is open; a socket nobody asked about
// for this long belongs to a page that closed.
const IDLE_MS = 90_000;
const PING_MS = 25_000;
const SWEEP_MS = 30_000;
const MAX_RETRY_MS = 60_000;

type Entry = {
  socket: WebSocket | null;
  lastInterestMs: number;
  retries: number;
  timer: number | null;
  ping: number | null;
  refused: boolean;
};

const entries = new Map<string, Entry>();
let started = false;

function idle(entry: Entry, nowMs: number): boolean {
  return nowMs - entry.lastInterestMs > IDLE_MS;
}

function close(eventKey: string): void {
  const entry = entries.get(eventKey);
  if (!entry) return;
  entries.delete(eventKey);
  if (entry.timer !== null) window.clearTimeout(entry.timer);
  if (entry.ping !== null) window.clearInterval(entry.ping);
  entry.socket?.close(1000);
}

function connect(eventKey: string, entry: Entry): void {
  entry.timer = null;
  if (entries.get(eventKey) !== entry || entry.refused || typeof WebSocket === 'undefined') return;
  let socket: WebSocket;
  try {
    socket = new WebSocket(liveResultsWebSocketUrl(eventKey));
  } catch {
    return;
  }
  entry.socket = socket;
  socket.onopen = () => {
    entry.retries = 0;
    entry.ping = window.setInterval(() => {
      if (socket.readyState === WebSocket.OPEN) socket.send('ping');
    }, PING_MS);
  };
  socket.onmessage = (message) => {
    let payload: { type?: string; version?: string | null } | null;
    try {
      payload = JSON.parse(String(message.data));
    } catch {
      return;
    }
    if (payload?.type !== 'results' || typeof payload.version !== 'string') return;
    if (applyLiveVersion(eventKey, payload.version)) {
      // Every poll on screen re-runs quietly (useSingleFlightPolling 'update').
      window.dispatchEvent(new CustomEvent('api:updated', { detail: { liveEvent: eventKey } }));
    }
  };
  socket.onclose = (event) => {
    if (entry.ping !== null) window.clearInterval(entry.ping);
    entry.ping = null;
    entry.socket = null;
    if (entries.get(eventKey) !== entry) return;
    // 4400: the server doesn't know the event; don't keep asking.
    if (event.code === 4400) {
      entry.refused = true;
      return;
    }
    if (idle(entry, Date.now())) {
      close(eventKey);
      return;
    }
    const delay = Math.min(MAX_RETRY_MS, 2000 * 2 ** entry.retries);
    entry.retries += 1;
    entry.timer = window.setTimeout(() => connect(eventKey, entry), delay * (0.8 + Math.random() * 0.4));
  };
}

function onInterest(eventKey: string): void {
  const nowMs = Date.now();
  const existing = entries.get(eventKey);
  if (existing) {
    existing.lastInterestMs = nowMs;
    return;
  }
  if (typeof navigator !== 'undefined' && navigator.onLine === false) return;
  const entry: Entry = { socket: null, lastInterestMs: nowMs, retries: 0, timer: null, ping: null, refused: false };
  entries.set(eventKey, entry);
  if (entries.size > MAX_SOCKETS) {
    const oldest = [...entries.entries()].sort((a, b) => a[1].lastInterestMs - b[1].lastInterestMs)[0];
    if (oldest) close(oldest[0]);
  }
  connect(eventKey, entry);
}

function sweep(): void {
  const nowMs = Date.now();
  for (const [eventKey, entry] of entries) {
    if (idle(entry, nowMs)) close(eventKey);
  }
}

export function startLiveResults(): () => void {
  if (started || typeof window === 'undefined') return () => undefined;
  started = true;
  setLiveInterestListener(onInterest);
  const sweeper = window.setInterval(sweep, SWEEP_MS);
  return () => {
    started = false;
    setLiveInterestListener(null);
    window.clearInterval(sweeper);
    for (const eventKey of [...entries.keys()]) close(eventKey);
  };
}

export function liveSocketCount(): number {
  return entries.size;
}
