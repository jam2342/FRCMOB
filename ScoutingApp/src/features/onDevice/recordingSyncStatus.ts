import type { StoredSession } from './offlineStore';

export type RecordingSyncFailure = {
  kind: 'connection' | 'server' | 'access' | 'rejected';
  status?: number;
  at: number;
};

// Store a bounded category, never raw server messages or authorization data.
export function classifyRecordingSyncFailure(error: unknown): RecordingSyncFailure {
  const status = error && typeof error === 'object' && 'status' in error && typeof error.status === 'number'
    ? error.status : undefined;
  const kind = status === 401 || status === 403 ? 'access'
    : status === 408 || status === 429 || (status !== undefined && status >= 500) ? 'server'
      : status !== undefined && status >= 400 && status < 500 ? 'rejected' : 'connection';
  return { kind, status, at: Date.now() };
}

export type RecordingSyncState = 'join-team' | 'other-team' | 'rejected' | 'access' | 'offline' | 'server' | 'connection' | 'waiting';

export function recordingSyncState(session: StoredSession, workspaceId: number | null, online: boolean): RecordingSyncState {
  if (workspaceId === null) return session.syncFailure?.kind === 'access' ? 'access' : 'join-team';
  if (session.workspaceId != null && session.workspaceId !== workspaceId) return 'other-team';
  if (session.syncFailure?.kind === 'rejected' || session.syncFailure?.kind === 'access') return session.syncFailure.kind;
  if (!online) return 'offline';
  return session.syncFailure?.kind ?? 'waiting';
}

export const RECORDING_SYNC_LABELS: Record<RecordingSyncState, string> = {
  'join-team': 'Join your team to upload this recording.',
  'other-team': 'Saved for another team. Switch back to that workspace to upload it.',
  rejected: 'The server did not accept this recording. It is still saved here. Retry after resolving the issue, or export a copy for recovery.',
  access: 'Team access needs attention. Rejoin the workspace this recording belongs to, then retry.',
  offline: 'Saved offline. It will upload when you reconnect.',
  server: 'The server is unavailable or busy. This recording is saved here and will retry automatically.',
  connection: 'The last upload lost its connection. This recording is saved here and will retry automatically.',
  waiting: 'Saved on this phone, waiting for the server to confirm the upload.',
};

export function recordingRecovery(session: StoredSession) {
  const { id, eventKey, matchKey, createdAt, workspaceId, workspaceName, payload, syncFailure } = session;
  return { id, eventKey, matchKey, createdAt, workspaceId, workspaceName, payload, syncFailure };
}
