import { isNativeApp } from '../../platform/runtime';
import { useCallback, useEffect, useRef, useState } from 'react';

import { useOnlineStatus } from '../../hooks/useOnlineStatus';
import { readStoredCenterContext } from '../../layout/centerContext';
import { subscribeWorkspaceSession } from '../../features/workspace/workspaceSession';
import {
  eventSavedAt,
  isEventPackReady,
  prepareEventOffline,
  prepareRecorderOffline,
  recorderReadyOffline,
  readSavedEventPack,
  verifySavedEventPack,
  type Progress,
  type SavedEventPack,
} from '../../features/offline/offlineReady';
import { Button } from './primitives';
import { SurfaceCard } from './SurfaceCard';
import './OfflineReadyCard.css';

function ago(ms: number): string {
  const min = Math.max(0, Math.round((Date.now() - ms) / 60_000));
  if (min < 1) return 'just now';
  if (min < 60) return `${min} min ago`;
  const h = Math.round(min / 60);
  return h < 48 ? `${h} h ago` : `${Math.round(h / 24)} days ago`;
}

function mb(bytes: number): string {
  return `${Math.max(0.1, bytes / 1e6).toFixed(bytes > 10e6 ? 0 : 1)} MB`;
}

function isIos(): boolean {
  return /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
}

function isStandalone(): boolean {
  return isNativeApp() || window.matchMedia?.('(display-mode: standalone)').matches || (navigator as { standalone?: boolean }).standalone === true;
}

// Events are usually run with little or no signal. This saves what a scout needs --
// the recorder's detector and the current event's data -- while there is Wi-Fi.
export function OfflineReadyCard({ compact = false }: { compact?: boolean }) {
  const { online } = useOnlineStatus();
  const eventKey = readStoredCenterContext().eventKey;
  const [recorderReady, setRecorderReady] = useState<boolean | null>(null);
  const [pack, setPack] = useState<SavedEventPack | null>(null);
  const [storageProtected, setStorageProtected] = useState<boolean | null>(null);
  const [storageLow, setStorageLow] = useState(false);
  const [progress, setProgress] = useState<Progress | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const verifySequence = useRef(0);

  useEffect(() => {
    let cancelled = false;
    const sequence = ++verifySequence.current;
    void recorderReadyOffline().then((ready) => {
      if (!cancelled) setRecorderReady(ready);
    });
    void verifySavedEventPack(eventKey ? readSavedEventPack(eventKey) : null).then((verified) => {
      if (!cancelled && sequence === verifySequence.current) setPack(verified);
    });
    void navigator.storage?.persisted?.().then((saved) => {
      if (!cancelled) setStorageProtected(saved);
    }).catch(() => undefined);
    void navigator.storage?.estimate?.().then(({ usage, quota }) => {
      if (!cancelled && typeof usage === 'number' && typeof quota === 'number') setStorageLow(quota - usage < 80_000_000);
    }).catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [eventKey]);

  useEffect(() => subscribeWorkspaceSession(() => {
    const sequence = ++verifySequence.current;
    setPack(null);
    void verifySavedEventPack(eventKey ? readSavedEventPack(eventKey) : null).then((verified) => {
      if (sequence === verifySequence.current) setPack(verified);
    });
  }), [eventKey]);

  const getReady = useCallback(async () => {
    setBusy(true);
    setError('');
    try {
      // ask the browser not to clear saved data under storage pressure
      const protectedNow = await navigator.storage?.persist?.().catch(() => false);
      if (typeof protectedNow === 'boolean') setStorageProtected(protectedNow);
      await prepareRecorderOffline((p) => setProgress({ ...p, label: `Downloading ${p.label}` }));
      setRecorderReady(await recorderReadyOffline());
      if (eventKey) {
        await prepareEventOffline(eventKey, (p) => setProgress({ ...p, label: `Saving ${p.label}` }));
        setPack(await verifySavedEventPack(readSavedEventPack(eventKey)));
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Something failed; try again on Wi-Fi.');
    } finally {
      setProgress(null);
      setBusy(false);
    }
  }, [eventKey]);

  const eventAt = eventKey ? eventSavedAt(eventKey) : null;
  const eventReady = isEventPackReady(pack);
  const allReady = recorderReady === true && (!eventKey || eventReady);
  const missing = pack?.steps.filter((step) => step.saved < step.total || step.savedAt === null) ?? [];
  const mustInstallFirst = isIos() && !isStandalone();

  return (
    <SurfaceCard
      title="Ready for no signal"
      subtitle="Most event venues have little or no reception. Save what you need here while you have Wi-Fi."
      expandable={false}
      mobileCollapsible={false}
      className={compact ? 'offline-ready offline-ready--compact' : 'offline-ready'}
    >
      <ul className="offline-ready__list">
        <li>
          <span className="offline-ready__item">Match recorder</span>
          <span className={recorderReady ? 'offline-ready__ok' : 'offline-ready__todo'}>
            {recorderReady === null ? 'Checking…' : recorderReady ? 'Saved on this phone' : 'Not saved yet'}
          </span>
        </li>
        <li>
          <span className="offline-ready__item">{eventKey ? `Event data (${eventKey.slice(0, 4)} ${eventKey.slice(4).toUpperCase()})` : 'Event data'}</span>
          <span className={eventReady ? 'offline-ready__ok' : 'offline-ready__todo'}>
            {!eventKey ? 'Pick your event on Events first' : eventReady && eventAt ? `${missing.length ? 'Core verified' : 'Verified'} · oldest data ${ago(eventAt)}` : pack ? 'Partly saved' : 'Not verified yet'}
          </span>
        </li>
      </ul>
      {pack ? (
        <div className="offline-ready__details">
          {['schedule', 'team pages', 'ratings', 'predictions'].map((label) => {
            const step = pack.steps.find((item) => item.label === label);
            return step ? <span key={label}>{label}: {step.saved}/{step.total} saved{step.savedAt ? ` · ${ago(step.savedAt)}` : ''}</span> : null;
          })}
          {missing.length ? <span>Needs retry: {missing.map((step) => step.label).join(', ')}</span> : null}
        </div>
      ) : null}
      {progress ? (
        <p className="offline-ready__progress" role="status">
          {progress.label}
          {progress.total > 1000 ? ` · ${mb(progress.done)} of ${mb(progress.total)}` : ` · ${progress.done} of ${progress.total}`}
        </p>
      ) : null}
      {error ? <p className="center-callout warning">{error}</p> : null}
      <div className="offline-ready__actions">
        <Button variant={allReady ? 'default' : 'primary'} onClick={() => void getReady()} loading={busy} disabled={busy || !online || mustInstallFirst}>
          {allReady ? 'Update saved data' : 'Get ready for offline'}
        </Button>
        {!online ? <span className="offline-ready__note">Connect to Wi-Fi to download.</span> : null}
      </div>
      {!compact && isIos() && !isStandalone() ? (
        <p className="offline-ready__note">
          First add FRCMOB to your Home Screen (Share → Add to Home Screen). Open it from the new icon, then save here. Safari and the Home Screen app keep separate saved data.
        </p>
      ) : null}
      {storageProtected === false ? <p className="offline-ready__note">This browser may remove saved files when storage is low. Recheck this card before the event.</p> : null}
      {storageLow ? <p className="offline-ready__note">This device has little browser storage available. Free some space before preparing the recorder.</p> : null}
    </SurfaceCard>
  );
}
