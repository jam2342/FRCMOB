// Bridge the offline store to the server sync endpoint. offlineStore.syncPendingSessions
// is endpoint-agnostic (takes a `post` callback so it stays decoupled + testable); this
// wires it to POST /tracks/on-device-session and flushes pending sessions on reconnect,
// mirroring utils/offlineQueue.startAutoFlush for the mutation queue.

import { syncOnDeviceSession } from '../../api';
import { openDb, syncPendingSessions, type StoredSession } from './offlineStore';

async function postSession(session: StoredSession): Promise<void> {
  await syncOnDeviceSession({
    id: session.id,
    eventKey: session.eventKey,
    matchKey: session.matchKey,
    createdAt: session.createdAt,
    payload: session.payload,
  });
}

// Flush every not-yet-synced on-device session to the server. Best-effort: a session
// whose POST fails stays pending for the next attempt. Returns the synced/failed counts.
export async function flushPendingOnDeviceSessions(): Promise<{ synced: number; failed: number }> {
  let db: IDBDatabase;
  try {
    db = await openDb();
  } catch {
    return { synced: 0, failed: 0 }; // IndexedDB unavailable — nothing to flush
  }
  try {
    return await syncPendingSessions(db, postSession);
  } finally {
    db.close();
  }
}

let _autoFlushBound = false;

// Replay pending on-device sessions whenever the device comes back online, plus once
// now if already online (to catch sessions stored on a prior visit). Bound once.
export function startOnDeviceSyncAutoFlush(): void {
  if (_autoFlushBound || typeof window === 'undefined') return;
  _autoFlushBound = true;
  window.addEventListener('online', () => {
    setTimeout(() => void flushPendingOnDeviceSessions(), 1500); // let the network settle
  });
  if (navigator.onLine) {
    setTimeout(() => void flushPendingOnDeviceSessions(), 2500);
  }
}
