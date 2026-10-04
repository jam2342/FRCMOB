import { afterEach, describe, expect, it, vi } from 'vitest';
const platform = vi.hoisted(() => ({ native: false }));
vi.mock('@capacitor/core', () => ({ Capacitor: { isNativePlatform: () => platform.native } }));
import { NATIVE_API_URL, NATIVE_WS_URL, nativeRouteFromUrl, resolveApiBaseUrl, resolveWebSocketBaseUrl } from './runtime';
afterEach(() => { platform.native = false; vi.unstubAllEnvs(); });
describe('native service endpoints', () => {
  it('uses public HTTPS endpoints instead of the app-local proxy, even with web build overrides', () => {
    platform.native = true;
    vi.stubEnv('VITE_API_URL', '/api');
    vi.stubEnv('VITE_WS_URL', '/api');
    expect(resolveApiBaseUrl()).toBe(NATIVE_API_URL);
    expect(resolveWebSocketBaseUrl(NATIVE_API_URL)).toBe(NATIVE_WS_URL);
  });
  it('rejects insecure or relative native endpoints before sending workspace credentials', () => {
    platform.native = true;
    vi.stubEnv('VITE_NATIVE_API_URL', 'http://example.com');
    expect(resolveApiBaseUrl).toThrow('HTTPS');
    vi.stubEnv('VITE_NATIVE_API_URL', '/api');
    expect(resolveApiBaseUrl).toThrow();
    vi.stubEnv('VITE_NATIVE_WS_URL', 'ws://example.com');
    expect(() => resolveWebSocketBaseUrl(NATIVE_API_URL)).toThrow('secure WebSocket');
  });
  it('preserves configured web endpoints', () => {
    vi.stubEnv('VITE_API_URL', '/api/');
    vi.stubEnv('VITE_WS_URL', 'wss://example.com/');
    expect(resolveApiBaseUrl()).toBe('/api');
    expect(resolveWebSocketBaseUrl('/api')).toBe('wss://example.com');
  });
});
describe('native app links', () => {
  it('accepts a local route with match context', () => {
    expect(nativeRouteFromUrl('frcmob://open#/scouting/record?event=2026test&match=2026test_qm1')).toBe('/scouting/record?event=2026test&match=2026test_qm1');
  });
  it.each(['https://evil.example/#/my-team', 'javascript:alert(1)', 'frcmob://other#/my-team', 'frcmob://open#//evil.example', 'frcmob://open#not-a-route'])('rejects external or malformed links: %s', url => {
    expect(nativeRouteFromUrl(url)).toBeNull();
  });
});
