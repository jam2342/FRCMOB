import { Capacitor } from '@capacitor/core';

export const isNativeApp = () => Capacitor.isNativePlatform();

// These are public service addresses, never credentials. The native app has no /api proxy.
export const NATIVE_API_URL = 'https://scouting-app-iryg.vercel.app/api';
export const NATIVE_WS_URL = 'wss://141-148-171-128.sslip.io';
const PUBLIC_SITE_URL = 'https://scouting-app-iryg.vercel.app/';

// A link someone else can open. The app routes on the hash (HashRouter), so a bare
// "/match-center?..." path lands on Home; the native app's own origin isn't reachable by anyone else.
export function shareableAppUrl(route: string): string {
  const base = isNativeApp() || typeof window === 'undefined' ? PUBLIC_SITE_URL : `${window.location.origin}/`;
  return `${base}#${route.startsWith('/') ? route : `/${route}`}`;
}

export function resolveApiBaseUrl(): string {
  const configured = String(isNativeApp() ? import.meta.env.VITE_NATIVE_API_URL || '' : import.meta.env.VITE_API_URL || import.meta.env.NEXT_PUBLIC_API_URL || '').trim();
  let base = configured || (isNativeApp() ? NATIVE_API_URL : import.meta.env.PROD ? '/api' : 'http://localhost:8000');
  if (isNativeApp()) {
    const url = new URL(base);
    if (url.protocol !== 'https:') throw new Error('Native apps require an HTTPS API endpoint.');
  } else if (typeof window !== 'undefined' && window.location.protocol === 'https:' && /^http:\/\//i.test(base)) {
    base = '/api';
  }
  return base.replace(/\/+$/, '');
}

export function resolveWebSocketBaseUrl(api: string): string {
  const configured = String(isNativeApp() ? import.meta.env.VITE_NATIVE_WS_URL || '' : import.meta.env.VITE_WS_URL || import.meta.env.NEXT_PUBLIC_WS_URL || '').trim();
  const base = configured || (isNativeApp() ? NATIVE_WS_URL : api);
  if (isNativeApp() && !/^wss:\/\//i.test(base)) throw new Error('Native apps require a secure WebSocket endpoint.');
  return base.replace(/\/+$/, '');
}

export function nativeRouteFromUrl(raw: string): string | null {
  try {
    const url = new URL(raw);
    if (url.protocol !== 'frcmob:' || url.hostname !== 'open') return null;
    const route = url.hash.slice(1);
    if (!/^\/(?!\/)[a-z0-9/-]*(?:\?[^#]*)?$/i.test(route)) return null;
    return route;
  } catch { return null; }
}
