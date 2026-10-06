import { coalesceRefresh } from '../onDevice/sessionChanges';
import { exportTextFile } from '../../platform/exportFile';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useOnlineStatus } from '../../hooks/useOnlineStatus';
import { flush, listFailedMutations, recoveryChanges } from '../../utils/offlineQueue';
import { listPendingSessions, openDb, type StoredSession } from '../onDevice/offlineStore';
import { getWorkspaceSession, subscribeWorkspaceSession } from '../workspace/workspaceSession';
import { RECORDING_SYNC_LABELS, recordingRecovery, recordingSyncState } from '../onDevice/recordingSyncStatus';
import './SyncStatusCard.css';
import { flushPendingOnDeviceSessions } from '../onDevice/sync';
import { Button } from '../../components/ui/primitives';
import { SurfaceCard } from '../../components/ui/SurfaceCard';
import { clearSyncIssue, lastConfirmedSync, lastSyncIssue } from './syncReceipt';

export function SyncStatusCard() {
  const { online, queueSize } = useOnlineStatus();
  const [recordings, setRecordings] = useState<StoredSession[] | null>(null);
  const [workspaceId, setWorkspaceId] = useState(() => getWorkspaceSession()?.workspace.id ?? null);
  const [readError, setReadError] = useState('');
  const refreshSequence = useRef(0);
  const flushRefreshRef = useRef<(() => Promise<void>) | null>(null);
  const [lastSync, setLastSync] = useState(lastConfirmedSync);
  const [issue, setIssue] = useState(lastSyncIssue);
  const [failedChanges, setFailedChanges] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const refresh = useCallback(async () => {
    const sequence = ++refreshSequence.current;
    const [saved, failed] = await Promise.allSettled([
      (async () => {
        const db = await openDb();
        try { return await listPendingSessions(db); }
        finally { db.close(); }
      })(),
      listFailedMutations(),
    ]);
    if (sequence !== refreshSequence.current) return;
    setRecordings(saved.status === 'fulfilled' ? saved.value : null);
    setFailedChanges(failed.status === 'fulfilled' ? failed.value.length : 0);
    setReadError(saved.status === 'rejected' ? 'Saved recordings could not be read on this phone. Try again before closing this app.'
      : failed.status === 'rejected' ? 'Saved changes could not be read. Try again before closing this app.' : '');
    setWorkspaceId(getWorkspaceSession()?.workspace.id ?? null);
    setLastSync(lastConfirmedSync());
    setIssue(lastSyncIssue());
  }, []);

  const invalidateRefresh = useCallback(() => { refreshSequence.current++; }, []);

  useEffect(() => {
    const { schedule: changed, cancel, flush: refreshNow } = coalesceRefresh(refresh);
    flushRefreshRef.current = refreshNow;
    void refreshNow();
    const unsubscribeWorkspace = subscribeWorkspaceSession(changed);
    window.addEventListener('offlinequeue:change', changed);
    window.addEventListener('frcmob:session-change', changed);
    window.addEventListener('frcmob:sync-receipt', changed);
    return () => {
      cancel();
      flushRefreshRef.current = null;
      invalidateRefresh();
      unsubscribeWorkspace();
      window.removeEventListener('offlinequeue:change', changed);
      window.removeEventListener('frcmob:session-change', changed);
      window.removeEventListener('frcmob:sync-receipt', changed);
    };
  }, [refresh, invalidateRefresh]);

  const sync = async () => {
    setBusy(true);
    setError('');
    try {
      const [, result] = await Promise.all([flush(), flushPendingOnDeviceSessions({ retryRejected: true })]);
      if (result.failed > 0) setError(`${result.failed} recording${result.failed === 1 ? '' : 's'} could not sync. They are still saved on this phone. Check their status below.`);
    } catch (err) { setError(err instanceof Error ? err.message : 'Sync failed. Try again when connected.'); }
    finally { await (flushRefreshRef.current?.() ?? refresh()); setBusy(false); }
  };

  const downloadRecovery = async () => {
    try {
      const changes = await recoveryChanges();
      await exportTextFile('frcmob-unsynced-changes.json', JSON.stringify({ version: 1, changes }, null, 2));
    } catch { setError('Could not export saved changes. Try again.'); }
  };

  const exportRecording = async (session: StoredSession) => {
    try {
      await exportTextFile('frcmob-unsynced-recording.json', JSON.stringify({ version: 1, recording: recordingRecovery(session) }, null, 2));
    } catch { setError('Could not export this recording. Try again.'); }
  };

  return (
    <SurfaceCard title="Saved on this phone" subtitle="Changes and recordings stay here until the server confirms them." expandable={false} mobileCollapsible={false}>
      <p>{queueSize} scouting change{queueSize === 1 ? '' : 's'} waiting · {recordings === null ? (readError ? 'Could not read recordings' : 'Checking recordings…') : `${recordings.length} recording${recordings.length === 1 ? '' : 's'} waiting`}</p>
      <p>{lastSync ? `Last confirmed sync: ${new Date(lastSync).toLocaleString()}` : 'No confirmed sync on this phone yet'}</p>
      {issue ? <p role="alert">Needs attention: {issue.count} change{issue.count === 1 ? '' : 's'} {issue.reason === 'conflict' ? 'conflicted with a teammate' : issue.reason === 'server-rejected' ? 'were rejected' : 'could not be saved'}. {issue.labels.slice(0, 3).join(', ')} <button type="button" onClick={clearSyncIssue}>Dismiss</button></p> : null}
      {failedChanges > 0 ? <p role="alert">{failedChanges} change{failedChanges === 1 ? '' : 's'} {failedChanges === 1 ? 'needs' : 'need'} attention. The original edits are still saved here. Export a copy, then check the current scouting entry or picklist before saving your correction. <Button onClick={() => void downloadRecovery()}>Export unsynced changes</Button></p> : null}
      {recordings?.length ? (
        <ul className="sync-recordings" aria-label="Waiting recordings">
          {recordings.map((session) => {
            const state = recordingSyncState(session, workspaceId, online);
            return (
              <li key={session.id}>
                <strong>{session.matchKey || 'Recording'}{session.eventKey ? ` · ${session.eventKey}` : ''}</strong>
                {session.workspaceName ? <p>Team workspace: {session.workspaceName}</p> : null}
                <p>{RECORDING_SYNC_LABELS[state]}</p>
                {session.syncFailure ? <p>Last upload attempt: {new Date(session.syncFailure.at).toLocaleString()}{session.syncFailure.status ? ` · server response ${session.syncFailure.status}` : ''}</p> : null}
                <a className="center-btn" href={`#/scouting/record?run=${encodeURIComponent(session.id)}`}>View results</a>
                <Button onClick={() => void exportRecording(session)}>Export recording</Button>
              </li>
            );
          })}
        </ul>
      ) : null}
      {readError ? <p role="alert">{readError}</p> : null}
      {error ? <p role="alert">{error}</p> : null}
      <Button onClick={() => void sync()} disabled={!online || busy} loading={busy}>Sync now</Button>
      {!online ? <p>Reconnect to send waiting work. Keep this app installed until waiting work is synced and changes needing attention are recovered.</p> : null}
    </SurfaceCard>
  );
}
