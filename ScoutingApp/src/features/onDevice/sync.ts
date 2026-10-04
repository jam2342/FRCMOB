// Bridge the offline store to the server sync endpoint. offlineStore.syncPendingSessions
// is endpoint-agnostic (takes a `post` callback so it stays decoupled + testable); this
// wires it to POST /tracks/on-device-session and flushes pending sessions on reconnect,
// mirroring utils/offlineQueue.startAutoFlush for the mutation queue.

import { syncOnDeviceSession } from '../../api';
import { openDb, saveSession, syncPendingSessions, type StoredSession } from './offlineStore';
import { getWorkspaceSession, getWorkspaceToken, subscribeWorkspaceSession } from '../workspace/workspaceSession';
import { recordConfirmedSync } from '../offline/syncReceipt';

async function postSession(session: StoredSession, workspaceId: number, workspaceToken: string) {
  return syncOnDeviceSession({
    id: session.id,
    eventKey: session.eventKey,
    matchKey: session.matchKey,
    createdAt: session.createdAt,
    workspaceId,
    payload: session.payload,
  }, workspaceToken);
}

// Flush every not-yet-synced on-device session to the server. Best-effort: a session
// whose POST fails stays pending for the next attempt. Returns the synced/failed counts.
let flushInFlight: Promise<{ synced: number; failed: number }> | null = null;

export function flushPendingOnDeviceSessions(options: { retryRejected?: boolean } = {}): Promise<{ synced: number; failed: number }> {
  if (flushInFlight) return flushInFlight;
  flushInFlight = flushSessions(options.retryRejected === true).finally(() => { flushInFlight = null; });
  return flushInFlight;
}

async function flushSessions(retryRejected: boolean): Promise<{ synced: number; failed: number }> {
  // Runs sync as a team member; without a workspace every POST would be refused.
  const workspace = getWorkspaceSession();
  const workspaceToken = workspace?.token ?? '';
  const workspaceId = workspace?.workspace.id ?? null;
  if (!workspace || !workspaceToken || workspaceId === null) return { synced: 0, failed: 0 };
  let db: IDBDatabase;
  try {
    db = await openDb();
  } catch {
    throw new Error('Saved recordings could not be read on this phone. Try again.');
  }
  try {
    // A run recorded in one team never uploads as another. Runs from before the
    // scout joined any team go to the team they join.
    const result = await syncPendingSessions(
      db,
      async (session) => {
        // Bind a recording made before joining a team before its first upload.
        // A lost acknowledgement must not allow a later retry under another team.
        if (session.workspaceId == null) {
          session = { ...session, workspaceId, workspaceName: workspace.workspace.name };
          await saveSession(db, session);
        }
        const current = getWorkspaceSession();
        if (current?.workspace.id !== workspaceId || current.token !== workspaceToken) throw new Error('Workspace changed before upload');
        return postSession(session, workspaceId, workspaceToken);
      },
      (session) => (session.workspaceId == null || session.workspaceId === workspaceId)
        && (retryRejected || session.syncFailure?.kind !== 'rejected'),
      () => {
        const current = getWorkspaceSession();
        return current?.workspace.id === workspaceId && current.token === workspaceToken;
      },
    );
    if (result.synced > 0) recordConfirmedSync();
    return result;
  } finally {
    db.close();
  }
}

let _autoFlushBound = false;

// Replay pending on-device sessions whenever the device comes back online, plus once
// now if already online (to catch sessions stored on a prior visit). Bound once.
function retrySync(): void {
  if (navigator.onLine) void flushPendingOnDeviceSessions().catch(() => {});
}

export function startOnDeviceSyncAutoFlush(): void {
  if (_autoFlushBound || typeof window === 'undefined') return;
  _autoFlushBound = true;
  window.addEventListener('online', () => {
    setTimeout(retrySync, 1500); // let the network settle
  });
  window.setInterval(retrySync, 30_000);
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') retrySync();
  });
  // Runs recorded before the scout joined their team go up as soon as they join.
  subscribeWorkspaceSession(() => {
    if (getWorkspaceToken()) retrySync();
  });
  if (navigator.onLine) {
    setTimeout(retrySync, 2500);
  }
}
