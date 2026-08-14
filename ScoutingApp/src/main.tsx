import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { Analytics } from '@vercel/analytics/react';
import './index.css';
import RootApp from './RootApp';
import { startAutoFlush } from './utils/offlineQueue';
import { startOnDeviceSyncAutoFlush } from './features/onDevice/sync';
import { preloadEventSearchIndex } from './utils/eventSearchIndex';
import { scheduleIdleWork } from './utils/idle';

// Register service worker only in production. In local dev, proactively
// unregister any stale worker/caches to avoid serving outdated Vite modules.
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    if (import.meta.env.PROD) {
      navigator.serviceWorker
        .register('/sw.js')
        .catch((err) => console.warn('[SW] registration failed:', err));
      return;
    }

    navigator.serviceWorker.getRegistrations()
      .then((registrations) => Promise.all(registrations.map((registration) => registration.unregister())))
      .catch((err) => console.warn('[SW] unregister failed:', err));

    if ('caches' in window) {
      caches.keys()
        .then((keys) => Promise.all(keys.map((key) => caches.delete(key))))
        .catch((err) => console.warn('[SW] cache cleanup failed:', err));
    }
  });
}

// Auto-replay queued mutations when the device comes back online.
startAutoFlush();
// Flush finished on-device match breakdowns to the server on reconnect.
startOnDeviceSyncAutoFlush();
// Warm the shared event-search index once the browser is idle.
scheduleIdleWork(() => {
  void preloadEventSearchIndex();
}, { fallbackDelayMs: 1500, timeoutMs: 2500 });

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <RootApp />
    <Analytics />
  </StrictMode>,
);
