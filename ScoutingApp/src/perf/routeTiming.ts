// "Time to first data" per route: from the moment a route opens (or the page
// starts loading) to its first API answer. api.ts calls markData on every
// successful GET; rum.ts reads the result. No React, no imports, so api.ts can use
// it without pulling anything in.

export type DataSource = 'network' | 'saved';
type Listener = (route: string, ms: number, source: DataSource) => void;

let currentRoute = '';
let routeStartedAt = 0;
let waiting = false;
let listener: Listener | null = null;

export function markRoute(route: string, startedAt: number): void {
  currentRoute = route;
  routeStartedAt = startedAt;
  waiting = true;
}

export function markData(source: DataSource): void {
  if (!waiting || !listener || typeof performance === 'undefined') return;
  waiting = false;
  listener(currentRoute, Math.max(0, performance.now() - routeStartedAt), source);
}

export function onFirstData(next: Listener | null): void {
  listener = next;
}
