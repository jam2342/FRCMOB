import { shouldPrefetchRoutes } from '../../routePrefetch';
import {
  loadEventsRankings,
  loadEventsTeamsHistory,
  loadMatchCenterSynergy,
  loadTeamCenterIntel,
  loadTeamCenterLogo,
} from './pageRequests';

// Starts a page's first requests when a finger touches a link to it (or a mouse
// rests on one), ~100-300 ms before the click lands and the page renders. The
// requests go through the normal API cache, so the page joins them instead of
// asking again. One listener for the whole app; links are ordinary anchors.

const HOVER_DELAY_MS = 60;
const REPEAT_AFTER_MS = 20_000;
const recent = new Map<string, number>();

type Prefetch = { key: string; run: () => Array<Promise<unknown>> };

export function prefetchForHref(href: string, base: string): Prefetch | null {
  let url: URL;
  try {
    url = new URL(href, base);
  } catch {
    return null;
  }
  if (url.origin !== new URL(base).origin || !url.hash.startsWith('#/')) return null;
  const [path, query = ''] = url.hash.slice(1).split('?');
  const params = new URLSearchParams(query);
  const event = (params.get('event') || '').trim().toLowerCase();
  if (path === '/team-center') {
    const team = (params.get('team') || '').trim().toLowerCase();
    if (!/^frc\d+[a-z]?$/.test(team)) return null;
    return {
      key: `team|${team}|${event}`,
      run: () => [
        loadTeamCenterIntel(team, event || undefined),
        loadTeamCenterLogo(team, event || undefined),
        import('../../pages/TeamCenterPage'),
      ],
    };
  }
  if (!/^\d{4}[a-z0-9]+$/.test(event)) return null;
  if (path === '/match-center') {
    return {
      key: `match|${event}`,
      run: () => [loadMatchCenterSynergy(event), import('../../pages/MatchCenterPage')],
    };
  }
  if (path === '/events') {
    return {
      key: `events|${event}`,
      run: () => [loadEventsTeamsHistory(event), loadEventsRankings(event), import('../../pages/EventsPage')],
    };
  }
  return null;
}

function prefetchFrom(target: EventTarget | null): void {
  const anchor = target instanceof Element ? target.closest('a[href]') : null;
  if (!anchor) return;
  const prefetch = prefetchForHref(anchor.getAttribute('href') || '', window.location.href);
  if (!prefetch) return;
  const now = Date.now();
  if (now - (recent.get(prefetch.key) ?? 0) < REPEAT_AFTER_MS) return;
  recent.set(prefetch.key, now);
  for (const pending of prefetch.run()) pending.catch(() => undefined);
}

let started = false;

export function startIntentPrefetch(): void {
  if (started || typeof document === 'undefined') return;
  const connection = (navigator as Navigator & { connection?: { saveData?: boolean; effectiveType?: string } }).connection;
  if (!shouldPrefetchRoutes(connection)) return;
  started = true;
  document.addEventListener('pointerdown', (event) => prefetchFrom(event.target), { capture: true, passive: true });
  document.addEventListener('focusin', (event) => prefetchFrom(event.target), { capture: true });
  let hoverTimer = 0;
  document.addEventListener('mouseover', (event) => {
    window.clearTimeout(hoverTimer);
    const target = event.target;
    hoverTimer = window.setTimeout(() => prefetchFrom(target), HOVER_DELAY_MS);
  }, { passive: true });
  document.addEventListener('mouseout', () => window.clearTimeout(hoverTimer), { passive: true });
}
