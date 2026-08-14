import { useEffect, useRef } from 'react';
import { scheduleIdleWork } from '../utils/idle';

/**
 * Prefetch likely pages when the user idles on the current page.
 * Uses requestIdleCallback (or setTimeout fallback) to avoid blocking.
 */
const importMap: Record<string, () => Promise<unknown>> = {
  '/home': () => import('../pages/HomePage'),
  '/events': () => import('../pages/EventsPage'),
  '/scouting': () => import('../pages/ScoutingPage'),
  '/scouting/assignments': () => import('../pages/ScoutingAssignPage'),
  '/scouting/auto-paths': () => import('../pages/AutoPathPage'),
  '/match-center': () => import('../pages/MatchCenterPage'),
  '/team-center': () => import('../pages/TeamCenterPage'),
};

const prefetchedSet = new Set<string>();

export function usePrefetchRoutes(currentPath: string) {
  const ran = useRef(false);

  useEffect(() => {
    if (ran.current) return;
    ran.current = true;

    scheduleIdleWork(() => {
      for (const [path, load] of Object.entries(importMap)) {
        if (path === currentPath || prefetchedSet.has(path)) continue;
        prefetchedSet.add(path);
        load().catch(() => {
          // silently ignore
        });
      }
    }, { fallbackDelayMs: 2000, timeoutMs: 2500 });
  }, [currentPath]);
}
