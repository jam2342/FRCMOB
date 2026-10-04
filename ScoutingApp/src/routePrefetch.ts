import { isNativeApp } from './platform/runtime';
// Pages load as separate chunks on first visit. Fetching the ones people move to
// next while the browser is idle makes those taps instant, and lets the service
// worker cache them, so a scout who never opened Scouting online can still open
// it at an event with no signal -- and events usually have none, so it is every page.
// The recorder's detector files (tens of MB) are separate: see features/offline.
const ROUTE_LOADERS: Array<() => Promise<unknown>> = [
  () => import('./pages/ScoutingPage'),
  () => import('./pages/MatchCenterPage'),
  () => import('./pages/TeamCenterPage'),
  () => import('./pages/EventsPage'),
  () => import('./pages/PitScoutingPage'),
  () => import('./pages/PicklistPage'),
  () => import('./pages/MyTeamPage'),
  () => import('./pages/ScoutingAssignPage'),
  () => import('./pages/HomePage'),
  () => import('./pages/OnDeviceRunPage'),
  () => import('./pages/AllianceAdvisorPage'),
  () => import('./pages/MatchPredictionPage'),
  () => import('./pages/StrategyBriefingPage'),
  () => import('./pages/ScoutingCoveragePage'),
  () => import('./pages/AutoPathPage'),
  () => import('./pages/ComparePage'),
  () => import('./pages/FavoritesPage'),
  () => import('./pages/SettingsPage'),
  () => import('./pages/FieldCalibrationPage'),
  () => import('./pages/ExportPage'),
  () => import('./pages/DataVizPage'),
  () => import('./pages/PrivacyPolicyPage'),
  () => import('./pages/TermsOfServicePage'),
];

const shellCache = 'frcmob-offline-shell-v1';
const shellManifestKey = 'frcmob-offline-shell-manifest-v1';

function currentEntry(): string | null {
  return document.querySelector<HTMLScriptElement>('script[type="module"][src]')?.src ?? null;
}

function savedShellUrls(): string[] | null {
  try {
    const value = JSON.parse(localStorage.getItem(shellManifestKey) || 'null') as { entry?: string; urls?: string[] } | null;
    return value?.entry === currentEntry() && Array.isArray(value.urls) ? value.urls : null;
  } catch { return null; }
}

export async function appShellReadyOffline(): Promise<boolean> {
  if (isNativeApp()) return true; // Every route chunk is installed in the app bundle.
  if (typeof caches === 'undefined') return false;
  const urls = savedShellUrls();
  if (!urls?.length) return false;
  const found = await Promise.all(urls.map(async (url) => Boolean(await caches.match(url))));
  return found.every(Boolean);
}

// Import every page while online, then explicitly save the resulting hashed
// assets. Service-worker cache writes are asynchronous; this waits for readback.
export async function prepareAppShellOffline(): Promise<void> {
  if (isNativeApp()) return;
  if (typeof caches === 'undefined') throw new Error('This browser cannot save app pages for offline use.');
  for (const load of ROUTE_LOADERS) await load();
  const urls = new Set<string>([
    '/', '/record.html', '/Heading.png', '/Heading.webp',
    '/fonts/ibm-plex-sans-latin.woff2', '/fonts/ibm-plex-sans-latin-ext.woff2',
  ]);
  const entry = currentEntry();
  if (!entry) throw new Error('The app entry file could not be identified.');
  urls.add(entry);
  for (const item of performance.getEntriesByType('resource')) {
    const url = new URL(item.name);
    if (url.origin === location.origin && /^\/assets\/.*\.(?:js|css)$/.test(url.pathname)) urls.add(url.href);
  }
  for (const link of document.querySelectorAll<HTMLLinkElement>('link[rel="stylesheet"][href]')) urls.add(link.href);
  const cache = await caches.open(shellCache);
  for (const url of urls) {
    const absolute = new URL(url, location.href).href;
    if (await caches.match(absolute)) continue;
    const response = await fetch(absolute);
    if (!response.ok) throw new Error(`App page ${absolute} answered ${response.status}`);
    await cache.put(absolute, response);
  }
  const absoluteUrls = [...urls].map((url) => new URL(url, location.href).href);
  localStorage.setItem(shellManifestKey, JSON.stringify({ entry, urls: absoluteUrls }));
  if (!(await appShellReadyOffline())) throw new Error('Some app pages did not finish saving. Try again on Wi-Fi.');
}

type NetworkInformationLike = { saveData?: boolean; effectiveType?: string };

export function shouldPrefetchRoutes(connection: NetworkInformationLike | undefined): boolean {
  if (!connection) return true;
  if (connection.saveData) return false;
  return !/(^|-)2g$/.test(connection.effectiveType ?? '');
}

let started = false;

export function prefetchRoutesWhenIdle(): void {
  if (started || typeof window === 'undefined') return;
  const connection = (navigator as Navigator & { connection?: NetworkInformationLike }).connection;
  if (!shouldPrefetchRoutes(connection)) return;
  started = true;
  const whenIdle =
    'requestIdleCallback' in window
      ? (callback: () => void) => window.requestIdleCallback(callback, { timeout: 8000 })
      : (callback: () => void) => window.setTimeout(callback, 3000);
  whenIdle(() => {
    // One at a time, so prefetching never competes with what the user asked for.
    void ROUTE_LOADERS.reduce<Promise<unknown>>(
      (chain, load) => chain.then(() => load().catch(() => undefined)),
      Promise.resolve(),
    );
  });
}
