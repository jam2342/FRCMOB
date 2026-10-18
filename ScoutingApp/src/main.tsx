// First: some modules read storage as they load (see platform/safeStorage.ts).
import './platform/storageGuard';
import { bootstrapNativeEphemeralAccess } from './api';
import { bootstrapWorkspaceSession, getWorkspaceSession } from './features/workspace/workspaceSession';
import { startPitDraftCleanup } from './features/offline/pitDrafts';
import { bootstrapNativeQueue } from './utils/offlineQueue';
import { isNativeApp } from './platform/runtime';
import { startNativeLifecycle } from './platform/nativeLifecycle';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { Analytics } from '@vercel/analytics/react';
import './index.css';
import RootApp from './RootApp';
import { startAutoFlush } from './utils/offlineQueue';
import { startOnDeviceSyncAutoFlush } from './features/onDevice/sync';
import { preloadEventSearchIndex } from './utils/eventSearchIndex';
import { scheduleIdleWork } from './utils/idle';
import { startRum, trackingAllowed } from './perf/rum';
import { startCspReports } from './perf/cspReports';
import { startIntentPrefetch } from './features/prefetch/intentPrefetch';
import { startMatchPreload } from './features/prefetch/matchPreload';
import { resolveApiBaseUrl } from './platform/runtime';

// Register service worker only in production. In local dev, proactively
// unregister any stale worker/caches to avoid serving outdated Vite modules.
if (!isNativeApp() && 'serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    if (import.meta.env.PROD) {
      navigator.serviceWorker
        .register('/sw.js')
        .catch((err) => console.warn('[SW] registration failed:', err));
      // Chunks this page loaded before the worker took over never went through it; hand
      // it the list so an offline reload still has them. Again later for lazy routes.
      const reportLoadedAssets = () => {
        const urls = performance.getEntriesByType('resource').map((entry) => entry.name).filter((name) => name.includes('/assets/'));
        navigator.serviceWorker.controller?.postMessage({ type: 'cache-loaded-assets', urls });
      };
      void navigator.serviceWorker.ready.then(() => {
        if (navigator.serviceWorker.controller) reportLoadedAssets();
        else navigator.serviceWorker.addEventListener('controllerchange', reportLoadedAssets, { once: true });
        setTimeout(reportLoadedAssets, 15_000);
      });
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

async function bootstrap() {
  bootstrapNativeEphemeralAccess();
  await bootstrapWorkspaceSession();
  await bootstrapNativeQueue();
  // Auto-replay queued mutations when the device comes back online.
  startAutoFlush();
  startPitDraftCleanup(() => getWorkspaceSession()?.workspace.id ?? null);
  // Flush finished on-device match breakdowns to the server on reconnect.
  startOnDeviceSyncAutoFlush();
  // Warm the shared event-search index once the browser is idle.
  scheduleIdleWork(() => {
    void preloadEventSearchIndex();
  }, { fallbackDelayMs: 1500, timeoutMs: 2500 });

  // Before the first render, so anything blocked while the page paints is counted.
  startCspReports(resolveApiBaseUrl());
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <RootApp />
      {/* Same opt-out as our own timings (DNT/GPC), so the Privacy Policy holds for both. */}
      {!isNativeApp() ? <Analytics beforeSend={(event) => (trackingAllowed() ? event : null)} /> : null}
    </StrictMode>,
  );

  void startNativeLifecycle().catch(() => { console.warn('Native navigation could not initialize.'); });
  startRum(resolveApiBaseUrl());
  startIntentPrefetch();
  startMatchPreload();
}
void bootstrap().catch(() => {
  const root = document.getElementById('root')!;
  root.replaceChildren();
  const message = document.createElement('p');
  message.textContent = 'FRCMOB could not unlock your saved team access. Your saved work is still here. Restart or try again.';
  const retry = document.createElement('button');
  retry.textContent = 'Try again';
  retry.onclick = () => window.location.reload();
  root.append(message, retry);
});
