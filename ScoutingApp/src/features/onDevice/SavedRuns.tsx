import { useEffect, useRef, useState } from 'react';
import { SurfaceCard } from '../../components/ui/SurfaceCard';
import { RunResults } from './RunResults';
import { useSavedRuns } from './useSavedRuns';

function requestedRun() {
  return new URLSearchParams(window.location.hash.split('?')[1] || '').get('run');
}

export function SavedRuns() {
  const { sessions, error, loading } = useSavedRuns();
  const [selectedId, setSelectedId] = useState<string | null>(requestedRun);
  const resultsRef = useRef<HTMLDivElement>(null);
  const selected = sessions.find((session) => session.id === selectedId);
  useEffect(() => {
    const changed = () => setSelectedId(requestedRun());
    window.addEventListener('hashchange', changed);
    return () => window.removeEventListener('hashchange', changed);
  }, []);
  const availableId = selected?.id;
  useEffect(() => {
    if (availableId) {
      resultsRef.current?.scrollIntoView?.({ block: 'start' });
      resultsRef.current?.querySelector<HTMLElement>('h3')?.focus({ preventScroll: true });
    }
  }, [availableId]);
  return (
    <SurfaceCard title="Saved runs on this device" subtitle="Reopen a run to see its robot heatmaps and any analysis saved at sync. Runs belong to the workspace that recorded them." expandable={false} mobileCollapsible={false}>
      {error ? <p role="alert">{error}</p> : null}
      {loading ? <p>Loading saved runs…</p> : sessions.length === 0 && !error ? <p>No saved runs for your current workspace on this device.</p> : null}
      <ul className="odr-saved-runs" aria-label="Saved runs">
        {sessions.map((session) => (
          <li key={session.id}>
            <div><strong>{session.matchKey || 'Recording'}</strong><p className="odr-hint">{new Date(session.createdAt).toLocaleString()} · {session.synced ? 'Synced' : 'Saved locally'}</p></div>
            <button type="button" className="center-btn" aria-label={`View results for ${session.matchKey || 'recording'} from ${new Date(session.createdAt).toLocaleString()}`} aria-expanded={selectedId === session.id} aria-controls="saved-run-results" onClick={() => setSelectedId(session.id)}>View results</button>
          </li>
        ))}
      </ul>
      {selectedId && !selected && !loading ? <p className="odr-hint">This run is not available in your current workspace on this device.</p> : null}
      {selected ? <div id="saved-run-results" ref={resultsRef} className="odr-saved-results"><RunResults session={selected} /></div> : null}
    </SurfaceCard>
  );
}
