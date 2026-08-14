import { useCallback, useEffect, useState } from 'react';
import {
  getPushPublicKey,
  sendPushTest,
  subscribePush,
  unsubscribePush,
} from '../../api';
import { readStoredCenterContext } from '../../layout/centerContext';
import { readFavoriteTeams } from '../../layout/userSettings';
import { SurfaceCard } from './SurfaceCard';
import './PushNotificationsCard.css';

const SCOUT_PROFILE_STORAGE = 'scouting_manual_profile_v1';
const PUSH_PREFS_STORAGE = 'scouting_push_prefs_v1';

type LocalPrefs = {
  match_lead_minutes: number;
  shift_alerts: boolean;
  room_key: string;
};

function readLocalPrefs(): LocalPrefs {
  try {
    const raw = window.localStorage.getItem(PUSH_PREFS_STORAGE);
    const parsed = raw ? JSON.parse(raw) : {};
    return {
      match_lead_minutes: Number(parsed?.match_lead_minutes) || 15,
      shift_alerts: Boolean(parsed?.shift_alerts),
      room_key: String(parsed?.room_key || ''),
    };
  } catch {
    return { match_lead_minutes: 15, shift_alerts: false, room_key: '' };
  }
}

function saveLocalPrefs(prefs: LocalPrefs): void {
  try {
    window.localStorage.setItem(PUSH_PREFS_STORAGE, JSON.stringify(prefs));
  } catch {
    // ignore
  }
}

function readScoutProfile(): string {
  try {
    return String(window.localStorage.getItem(SCOUT_PROFILE_STORAGE) || '').trim();
  } catch {
    return '';
  }
}

function urlBase64ToUint8Array(base64String: string): Uint8Array {
  const padding = '='.repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
  const rawData = window.atob(base64);
  const output = new Uint8Array(rawData.length);
  for (let i = 0; i < rawData.length; i += 1) output[i] = rawData.charCodeAt(i);
  return output;
}

function pushSupported(): boolean {
  return (
    typeof window !== 'undefined' &&
    'serviceWorker' in navigator &&
    'PushManager' in window &&
    'Notification' in window
  );
}

export function PushNotificationsCard() {
  const supported = pushSupported();
  const [serverConfigured, setServerConfigured] = useState<boolean | null>(null);
  const [publicKey, setPublicKey] = useState<string>('');
  const [subscribed, setSubscribed] = useState(false);
  const [endpoint, setEndpoint] = useState('');
  const [busy, setBusy] = useState(false);
  const [statusText, setStatusText] = useState('');
  const [errorText, setErrorText] = useState('');
  const [prefs, setPrefs] = useState<LocalPrefs>(() =>
    typeof window === 'undefined'
      ? { match_lead_minutes: 15, shift_alerts: false, room_key: '' }
      : readLocalPrefs(),
  );

  useEffect(() => {
    if (!supported) return;
    let cancelled = false;
    void (async () => {
      try {
        const config = await getPushPublicKey();
        if (cancelled) return;
        setServerConfigured(Boolean(config.configured));
        setPublicKey(config.public_key || '');
      } catch {
        if (!cancelled) setServerConfigured(false);
      }
      try {
        const registration = await navigator.serviceWorker.ready;
        const subscription = await registration.pushManager.getSubscription();
        if (!cancelled && subscription) {
          setSubscribed(true);
          setEndpoint(subscription.endpoint);
        }
      } catch {
        // No active SW (dev mode) — handled by the support note below.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [supported]);

  const updatePrefs = useCallback((update: Partial<LocalPrefs>) => {
    setPrefs((prev) => {
      const next = { ...prev, ...update };
      saveLocalPrefs(next);
      return next;
    });
  }, []);

  const syncSubscription = useCallback(
    async (subscription: PushSubscription, currentPrefs: LocalPrefs) => {
      const json = subscription.toJSON();
      const context = readStoredCenterContext();
      await subscribePush({
        endpoint: subscription.endpoint,
        keys: {
          p256dh: String(json.keys?.p256dh || ''),
          auth: String(json.keys?.auth || ''),
        },
        event_key: context.eventKey || null,
        team_keys: readFavoriteTeams(),
        prefs: {
          match_lead_minutes: currentPrefs.match_lead_minutes,
          shift_alerts: currentPrefs.shift_alerts,
          scout_profile: readScoutProfile(),
          room_key: currentPrefs.room_key.trim().toLowerCase(),
        },
      });
    },
    [],
  );

  async function handleEnable() {
    setBusy(true);
    setErrorText('');
    setStatusText('');
    try {
      const permission = await Notification.requestPermission();
      if (permission !== 'granted') {
        setErrorText('Notification permission was denied. Enable it in your browser settings.');
        return;
      }
      const registration = await navigator.serviceWorker.ready;
      let subscription = await registration.pushManager.getSubscription();
      if (!subscription) {
        subscription = await registration.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: urlBase64ToUint8Array(publicKey) as BufferSource,
        });
      }
      await syncSubscription(subscription, prefs);
      setSubscribed(true);
      setEndpoint(subscription.endpoint);
      setStatusText('Match alerts enabled for this device.');
    } catch (err) {
      setErrorText((err as Error).message || 'Failed to enable notifications.');
    } finally {
      setBusy(false);
    }
  }

  async function handleDisable() {
    setBusy(true);
    setErrorText('');
    try {
      const registration = await navigator.serviceWorker.ready;
      const subscription = await registration.pushManager.getSubscription();
      const currentEndpoint = subscription?.endpoint || endpoint;
      if (subscription) await subscription.unsubscribe();
      if (currentEndpoint) await unsubscribePush(currentEndpoint);
      setSubscribed(false);
      setEndpoint('');
      setStatusText('Notifications disabled.');
    } catch (err) {
      setErrorText((err as Error).message || 'Failed to disable notifications.');
    } finally {
      setBusy(false);
    }
  }

  async function handleSavePrefs() {
    if (!subscribed) return;
    setBusy(true);
    setErrorText('');
    try {
      const registration = await navigator.serviceWorker.ready;
      const subscription = await registration.pushManager.getSubscription();
      if (!subscription) {
        setSubscribed(false);
        setErrorText('Subscription expired — enable notifications again.');
        return;
      }
      await syncSubscription(subscription, prefs);
      setStatusText('Notification preferences updated.');
    } catch (err) {
      setErrorText((err as Error).message || 'Failed to update preferences.');
    } finally {
      setBusy(false);
    }
  }

  async function handleTest() {
    if (!endpoint) return;
    setBusy(true);
    setErrorText('');
    try {
      await sendPushTest(endpoint);
      setStatusText('Test notification sent.');
    } catch (err) {
      setErrorText((err as Error).message || 'Test notification failed.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <SurfaceCard
      title="Match Alerts"
      subtitle="Push notifications before your favorite teams play and before your scouting shifts."
    >
      {!supported ? (
        <p className="center-callout muted">
          This browser does not support push notifications. On iPhone/iPad, install the app to
          your home screen first (Share → Add to Home Screen).
        </p>
      ) : serverConfigured === false ? (
        <p className="center-callout muted">
          Push is not configured on the server (VAPID keys missing) — ask your team admin.
        </p>
      ) : (
        <>
          <p className="push-alerts-note">
            Alerts go to this device for the teams on your Favorites list, at your current event.
          </p>
          <div className="push-alerts-actions">
            {!subscribed ? (
              <button
                type="button"
                className="center-btn"
                onClick={() => void handleEnable()}
                disabled={busy || !publicKey}
              >
                {busy ? 'Working…' : 'Enable match alerts'}
              </button>
            ) : (
              <>
                <button
                  type="button"
                  className="center-btn ghost"
                  onClick={() => void handleTest()}
                  disabled={busy}
                >
                  Send test
                </button>
                <button
                  type="button"
                  className="center-btn ghost danger"
                  onClick={() => void handleDisable()}
                  disabled={busy}
                >
                  Disable
                </button>
              </>
            )}
          </div>

          <div className="push-alerts-form">
            <label className="push-alerts-field push-alerts-field--short">
              <span>Lead time (minutes before match)</span>
              <input
                className="center-input"
                type="number"
                min={1}
                max={60}
                value={prefs.match_lead_minutes}
                onChange={(event) =>
                  updatePrefs({
                    match_lead_minutes: Math.max(1, Math.min(60, Number(event.target.value) || 15)),
                  })
                }
              />
            </label>
            <label className="push-alerts-checkbox">
              <input
                type="checkbox"
                checked={prefs.shift_alerts}
                onChange={(event) => updatePrefs({ shift_alerts: event.target.checked })}
              />
              <span>Also alert before my scouting shifts</span>
            </label>
            {prefs.shift_alerts ? (
              <label className="push-alerts-field push-alerts-field--room">
                <span>Scouting room key</span>
                <input
                  className="center-input"
                  type="text"
                  placeholder="e.g. tiger-42"
                  value={prefs.room_key}
                  onChange={(event) => updatePrefs({ room_key: event.target.value })}
                />
              </label>
            ) : null}
            {subscribed ? (
              <button
                type="button"
                className="center-btn"
                onClick={() => void handleSavePrefs()}
                disabled={busy}
              >
                Save preferences
              </button>
            ) : null}
          </div>
        </>
      )}
      {errorText ? <p className="center-callout warning">{errorText}</p> : null}
      {statusText ? <p className="center-success-text">{statusText}</p> : null}
    </SurfaceCard>
  );
}
