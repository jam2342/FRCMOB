import { abortableFetch } from './abortableFetch';
import { isNativeApp, nativeRouteFromUrl } from './runtime';

export async function startNativeLifecycle(): Promise<() => void> {
  if (!isNativeApp()) return () => undefined;
  document.documentElement.classList.add('native-app');
  const originalFetch = window.fetch;
  const nativeFetch = abortableFetch(originalFetch.bind(window));
  window.fetch = nativeFetch;
  const [{ App }, { Browser }] = await Promise.all([import('@capacitor/app'), import('@capacitor/browser')]);
  const followLink = (url: string) => {
    const route = nativeRouteFromUrl(url);
    if (route) window.location.hash = route;
  };
  const handles = await Promise.all([
    App.addListener('appUrlOpen', ({ url }) => followLink(url)),
    App.addListener('appStateChange', ({ isActive }) => {
      if (isActive) window.dispatchEvent(new Event('online')); // recheck health and replay pending work
    }),
    App.addListener('backButton', ({ canGoBack }) => {
      if (canGoBack) window.history.back();
      else if (window.location.hash && window.location.hash !== '#/') window.location.hash = '/';
      else void App.minimizeApp();
    }),
  ]);
  const launch = await App.getLaunchUrl();
  if (launch?.url) followLink(launch.url);
  const openExternal = (event: MouseEvent) => {
    if (event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    const anchor = event.target instanceof Element ? event.target.closest<HTMLAnchorElement>('a[href]') : null;
    if (!anchor || anchor.hasAttribute('download')) return;
    const url = new URL(anchor.href, window.location.href);
    if (!['https:', 'http:'].includes(url.protocol) || url.origin === window.location.origin) return;
    event.preventDefault();
    void Browser.open({ url: url.href }).catch(() => { window.alert('This link could not open. Please try again.'); });
  };
  const storageError = () => window.alert('Team access could not be updated in secure storage. Your saved scouting work is still here. Restart the app to try again.');
  window.addEventListener('frcmob:secure-storage-error', storageError);
  document.addEventListener('click', openExternal);
  return () => {
    window.removeEventListener('frcmob:secure-storage-error', storageError);
    document.removeEventListener('click', openExternal);
    if (window.fetch === nativeFetch) window.fetch = originalFetch;
    for (const handle of handles) void handle.remove();
  };
}
