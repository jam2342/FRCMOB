import type { Metric } from 'web-vitals';
import { isNativeApp } from '../platform/runtime';
import { markRoute, onFirstData, type DataSource } from './routeTiming';

// Anonymous page-speed timings from real visitors, sent once when the tab is
// hidden. What goes out: the route *pattern* (never a team, event or match key),
// the metric, its value, a device class, the connection type and how the app runs.
// The backend keeps counts only (routes_telemetry.py). Off in dev and tests, and
// when the browser asks not to be tracked.

type Sample = {
  route: string;
  metric: string;
  value: number;
  device: 'phone' | 'tablet' | 'desktop';
  connection: string;
  display: 'browser' | 'standalone' | 'native';
  source: 'network' | 'saved' | 'unknown';
};

const MAX_QUEUE = 40;
const CONNECTIONS = new Set(['slow-2g', '2g', '3g', '4g']);
let queue: Sample[] = [];
let endpoint = '';
let started = false;

// '/team-center?team=frc254' -> '/team-center'; any segment carrying a digit (a key)
// becomes 'id', and only the first three segments count.
export function routePattern(hash: string): string {
  const path = String(hash || '').replace(/^#/, '').split('?')[0] || '/';
  const segments = path
    .split('/')
    .filter(Boolean)
    .slice(0, 3)
    .map((segment) => (/\d/.test(segment) ? 'id' : segment.toLowerCase().replace(/[^a-z-]/g, '').slice(0, 40) || 'id'));
  return `/${segments.join('/')}`;
}

function deviceClass(): Sample['device'] {
  const width = typeof window !== 'undefined' ? window.innerWidth : 1280;
  if (width < 600) return 'phone';
  if (width < 1024) return 'tablet';
  return 'desktop';
}

function connectionType(): string {
  const effective = (navigator as Navigator & { connection?: { effectiveType?: string } }).connection?.effectiveType;
  return effective && CONNECTIONS.has(effective) ? effective : 'unknown';
}

function displayMode(): Sample['display'] {
  if (isNativeApp()) return 'native';
  const standalone = window.matchMedia?.('(display-mode: standalone)').matches
    || (navigator as Navigator & { standalone?: boolean }).standalone === true;
  return standalone ? 'standalone' : 'browser';
}

function push(route: string, metric: string, value: number, source: Sample['source'] = 'unknown'): void {
  if (!Number.isFinite(value) || value < 0) return;
  queue.push({
    route,
    metric,
    value: metric === 'CLS' ? Math.round(value * 1000) / 1000 : Math.round(value),
    device: deviceClass(),
    connection: connectionType(),
    display: displayMode(),
    source,
  });
  if (queue.length >= MAX_QUEUE) flush();
}

function flush(): void {
  if (!queue.length || !endpoint) return;
  const body = JSON.stringify({ samples: queue.slice(0, 50) });
  queue = [];
  try {
    // text/plain keeps it a simple request (no preflight) for the native shells'
    // cross-origin API.
    const blob = new Blob([body], { type: 'text/plain' });
    if (navigator.sendBeacon?.(endpoint, blob)) return;
    void fetch(endpoint, { method: 'POST', body, keepalive: true, headers: { 'Content-Type': 'text/plain' } }).catch(() => undefined);
  } catch {
    // Telemetry never gets in the way.
  }
}

export function trackingAllowed(): boolean {
  const nav = navigator as Navigator & { globalPrivacyControl?: boolean; doNotTrack?: string };
  return !(nav.globalPrivacyControl === true || nav.doNotTrack === '1');
}

export function startRum(apiBase: string): void {
  if (started || typeof window === 'undefined') return;
  if (import.meta.env.DEV || import.meta.env.MODE === 'test' || !trackingAllowed()) return;
  started = true;
  endpoint = `${apiBase}/telemetry/vitals`;

  // Load metrics belong to the route the page was opened on.
  const landing = routePattern(window.location.hash);
  const report = (metric: Metric) => push(metric.name === 'INP' ? routePattern(window.location.hash) : landing, metric.name, metric.value);
  // Its own chunk, off the startup path: the browser buffers the entries these read, so
  // a library that arrives a moment after the page still sees the whole load.
  void import('web-vitals').then(({ onCLS, onFCP, onINP, onLCP, onTTFB }) => {
    onLCP(report);
    onFCP(report);
    onTTFB(report);
    onCLS(report);
    onINP(report);
  }).catch(() => undefined);

  onFirstData((route: string, ms: number, source: DataSource) => push(route, 'TTFD', ms, source));
  markRoute(landing, 0);
  window.addEventListener('hashchange', () => {
    const route = routePattern(window.location.hash);
    markRoute(route, performance.now());
  });

  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') flush();
  });
  window.addEventListener('pagehide', flush);
}
