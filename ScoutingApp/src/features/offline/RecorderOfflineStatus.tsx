import { useEffect, useState } from 'react';

import { useOnlineStatus } from '../../hooks/useOnlineStatus';
import { shouldPrefetchRoutes } from '../../routePrefetch';
import { prepareRecorderOffline, recorderReadyOffline } from './offlineReady';

type State = 'checking' | 'ready' | 'saving' | 'missing';

export function RecorderOfflineStatus() {
  const { online } = useOnlineStatus();
  const [state, setState] = useState<State>('checking');
  const [pct, setPct] = useState<number | null>(null);
  const [storageFull, setStorageFull] = useState(false);
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      if (await recorderReadyOffline()) {
        if (!cancelled) setState('ready');
        return;
      }
      const connection = (navigator as Navigator & { connection?: { saveData?: boolean; effectiveType?: string } }).connection;
      if (!online || !shouldPrefetchRoutes(connection)) {
        if (!cancelled) setState('missing');
        return;
      }
      if (!cancelled) setState('saving');
      await prepareRecorderOffline((p) => {
        if (!cancelled && p.total > 0) setPct(Math.round((100 * p.done) / p.total));
      });
      if (!cancelled) setState((await recorderReadyOffline()) ? 'ready' : 'missing');
    })().catch((error: unknown) => {
      if (!cancelled) {
        setStorageFull(typeof error === 'object' && error !== null && 'name' in error && error.name === 'QuotaExceededError');
        setState('missing');
      }
    });
    return () => { cancelled = true; };
  }, [online, retry]);

  if (state === 'checking') return null;
  return (
    <div>
      <p className={state === 'ready' ? 'odr-hint odr-offline odr-offline--ready' : 'odr-hint odr-offline'} role="status">
        {state === 'ready'
          ? 'Detector saved on this phone: works with no signal.'
          : state === 'saving'
            ? `Saving the detector for offline use${pct !== null ? ` · ${pct}%` : '…'}`
            : storageFull
              ? 'Device storage is full. Free up space, then retry saving the detector.'
              : 'Detector not saved on this phone yet. Open this page once on Wi-Fi before the event.'}
      </p>
      {state === 'missing' && online ? (
        <button className="center-btn ghost" type="button" onClick={() => {
          setStorageFull(false);
          setPct(null);
          setState('checking');
          setRetry(current => current + 1);
        }}>Retry saving detector</button>
      ) : null}
    </div>
  );
}
