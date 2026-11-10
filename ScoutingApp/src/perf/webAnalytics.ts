import { isNativeApp } from '../platform/runtime';
import { trackingAllowed } from './rum';
import { WEB_ANALYTICS_SCRIPT_ORIGIN } from '../config/analyticsHosts';

// Cloudflare Web Analytics: page views without cookies or local storage. The token
// is public by design (it ships in every page) and only names the site.
const BEACON_SRC = `${WEB_ANALYTICS_SCRIPT_ORIGIN}/beacon.min.js`;
const SITE_TOKEN = '33bc009e074e4046a183f8bdaa77bad0';
export const WEB_ANALYTICS_HOST = 'frcmob.com';

let started = false;

// Same opt-out as our own timings (DNT/GPC), so the Privacy Policy holds for both.
export function startWebAnalytics(): void {
  if (started || typeof document === 'undefined') return;
  if (isNativeApp() || import.meta.env.DEV || import.meta.env.MODE === 'test' || !trackingAllowed()) return;
  // Only the real site: previews and the old address would count as someone else.
  if (window.location.hostname !== WEB_ANALYTICS_HOST) return;
  started = true;
  const script = document.createElement('script');
  script.defer = true;
  script.src = BEACON_SRC;
  script.setAttribute('data-cf-beacon', JSON.stringify({ token: SITE_TOKEN, spa: true }));
  document.head.appendChild(script);
}

// Vercel Web Analytics only works on Vercel; elsewhere its script path is a 404.
export function onVercelHost(): boolean {
  return typeof window !== 'undefined' && window.location.hostname.endsWith('.vercel.app');
}
