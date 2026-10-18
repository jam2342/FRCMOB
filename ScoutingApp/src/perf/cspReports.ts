import { routePattern, trackingAllowed } from './rum';

// Tells the backend what the browser's Content-Security-Policy blocked, so a new
// outside service the policy doesn't list shows up on the ops dashboard instead of
// as a quietly broken embed. The policy is a <meta> tag (build/contentSecurityPolicy.ts),
// which can't send reports itself. What goes out: the route pattern, the directive
// and the blocked origin (never a path or query). Same opt-outs as perf/rum.ts.

export type CspReport = { page: string; directive: string; blocked: string };

const MAX_PER_PAGE_LOAD = 10;
// Extensions inject their own scripts and styles; their blocks aren't ours to fix.
const EXTENSION_SCHEMES = /^(chrome|moz|safari|safari-web|ms-browser)-extension:/;

export function describeViolation(event: Pick<SecurityPolicyViolationEvent, 'effectiveDirective' | 'violatedDirective' | 'blockedURI' | 'sourceFile'>, hash: string): CspReport | null {
  const blockedRaw = String(event.blockedURI || '').trim();
  if (EXTENSION_SCHEMES.test(blockedRaw) || EXTENSION_SCHEMES.test(String(event.sourceFile || ''))) return null;
  const directive = String(event.effectiveDirective || event.violatedDirective || '').split(' ')[0].toLowerCase();
  if (!directive) return null;
  let blocked = blockedRaw.toLowerCase();
  if (!['inline', 'eval', 'wasm-eval', 'data', 'blob', 'self'].includes(blocked)) {
    try {
      const url = new URL(blockedRaw);
      blocked = url.protocol === 'data:' ? 'data' : url.protocol === 'blob:' ? 'blob' : url.origin;
    } catch {
      blocked = 'other';
    }
  }
  return { page: routePattern(hash), directive, blocked };
}

export function startCspReports(apiBase: string): void {
  if (typeof document === 'undefined') return;
  if (import.meta.env.DEV || import.meta.env.MODE === 'test' || !trackingAllowed()) return;
  const endpoint = `${apiBase}/telemetry/csp`;
  const seen = new Set<string>();
  let pending: CspReport[] = [];
  let timer: ReturnType<typeof setTimeout> | null = null;

  const flush = () => {
    timer = null;
    if (!pending.length) return;
    const body = JSON.stringify({ violations: pending });
    pending = [];
    try {
      const blob = new Blob([body], { type: 'text/plain' });
      if (navigator.sendBeacon?.(endpoint, blob)) return;
      void fetch(endpoint, { method: 'POST', body, keepalive: true, headers: { 'Content-Type': 'text/plain' } }).catch(() => undefined);
    } catch {
      // Reporting never gets in the way.
    }
  };

  document.addEventListener('securitypolicyviolation', (event) => {
    // Report-only policies would also land here; only what was really blocked counts.
    if (event.disposition && event.disposition !== 'enforce') return;
    const report = describeViolation(event, window.location.hash);
    if (!report) return;
    const key = `${report.page}|${report.directive}|${report.blocked}`;
    if (seen.has(key) || seen.size >= MAX_PER_PAGE_LOAD) return;
    seen.add(key);
    pending.push(report);
    // A blocked embed often fires several events at once; send them together.
    if (!timer) timer = setTimeout(flush, 2000);
  });
  window.addEventListener('pagehide', flush);
}
