import { useEffect, useState, useSyncExternalStore } from 'react';
import { listSessions, openDb, type StoredSession } from './offlineStore';
import { getWorkspaceSession, subscribeWorkspaceSession } from '../workspace/workspaceSession';

export function useSavedRuns() {
  const workspaceId = useSyncExternalStore(subscribeWorkspaceSession, () => getWorkspaceSession()?.workspace.id ?? null);
  const [sessions, setSessions] = useState<StoredSession[]>([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let active = true;
    let sequence = 0;
    const refresh = async () => {
      const request = ++sequence;
      try {
        const db = await openDb();
        let saved: StoredSession[];
        try { saved = await listSessions(db); }
        finally { db.close(); }
        if (!active || request !== sequence) return;
        setSessions(saved.sort((a, b) => b.createdAt - a.createdAt));
        setError('');
      } catch {
        if (active && request === sequence) setError('Saved runs could not be read on this device. Reopen this page to try again.');
      } finally {
        if (active && request === sequence) setLoading(false);
      }
    };
    void refresh();
    const changed = () => { void refresh(); };
    window.addEventListener('frcmob:session-change', changed);
    return () => { active = false; window.removeEventListener('frcmob:session-change', changed); };
  }, []);
  return { sessions: sessions.filter((session) => session.workspaceId == null || session.workspaceId === workspaceId), error, loading };
}
