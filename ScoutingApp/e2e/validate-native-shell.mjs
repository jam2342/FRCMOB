// Local bridge fixture. This verifies the shared UI's native branches, not a real WebView.
import assert from 'node:assert/strict';
import { chromium } from 'playwright';
const base = process.env.NATIVE_TEST_URL || 'http://127.0.0.1:4179';
assert.ok(['localhost', '127.0.0.1'].includes(new URL(base).hostname));
const browser = await chromium.launch({ channel: 'chrome' });
try {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, serviceWorkers: 'allow' });
  const page = await context.newPage();
  const errors = []; const apiRequests = [];
  page.on('pageerror', error => errors.push(error.message));
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.pathname.includes('/api/')) {
      apiRequests.push(url.href);
      return route.fulfill({ json: { ok: true }, headers: { 'access-control-allow-origin': '*', 'access-control-allow-headers': '*', 'access-control-allow-methods': '*' } });
    }
    if (url.origin !== new URL(base).origin) return route.abort();
    return route.continue();
  });
  await page.addInitScript(() => {
    window.androidBridge = {};
    window.__nativeCalls = [];
    window.__nativeListeners = {};
    window.Capacitor = {
      PluginHeaders: [
        { name: 'App', methods: [{ name: 'addListener', rtype: 'callback' }, { name: 'removeListener', rtype: 'promise' }, { name: 'getLaunchUrl', rtype: 'promise' }, { name: 'minimizeApp', rtype: 'promise' }] },
        { name: 'Browser', methods: [{ name: 'open', rtype: 'promise' }] },
        { name: 'Filesystem', methods: [{ name: 'writeFile', rtype: 'promise' }] },
        { name: 'SecureStorage', methods: ['get', 'set', 'remove', 'encrypt', 'decrypt'].map(name => ({ name, rtype: 'promise' })) },
        { name: 'Share', methods: [{ name: 'share', rtype: 'promise' }] },
      ],
      nativePromise: async (plugin, method, options) => {
        window.__nativeCalls.push({ plugin, method, options });
        if (plugin === 'SecureStorage') {
          if (method === 'get') return { value: null };
          if (method === 'encrypt') return { value: btoa(options.value) };
          if (method === 'decrypt') return { value: atob(options.value) };
          return {};
        }
        return plugin === 'Filesystem' ? { uri: 'file:///fixture-cache/recovery.json' } : {};
      },
      nativeCallback: (plugin, method, options, callback) => {
        if (plugin === 'App' && method === 'addListener') window.__nativeListeners[options.eventName] = callback;
        return Promise.resolve('fixture-listener');
      },
    };
    localStorage.setItem('frcmob_tour_prompt_dismissed_v1', '1');
  });
  await page.goto(`${base}/#/my-team`);
  await page.getByText('Join your team', { exact: true }).waitFor();
  await page.waitForFunction(() => Boolean(window.__nativeListeners.backButton));
  await page.getByText('Saved on this phone', { exact: true }).first().waitFor();
  assert.equal(await page.evaluate(async () => (await navigator.serviceWorker.getRegistrations()).length), 0);
  await page.evaluate(async () => {
    const db = await new Promise((resolve, reject) => {
      const request = indexedDB.open('frcmob-ondevice', 1);
      request.onsuccess = () => resolve(request.result); request.onerror = () => reject(request.error);
    });
    const tx = db.transaction('sessions', 'readwrite');
    tx.objectStore('sessions').put({ id: 'fixture-native-recording', matchKey: '2026test_qm1', eventKey: '2026test', createdAt: 1, synced: false, payload: { pointsByTeam: { frc254: [{ x: 1, y: 2, t: 3 }] } } });
    await new Promise((resolve, reject) => { tx.oncomplete = resolve; tx.onerror = () => reject(tx.error); });
    db.close(); window.dispatchEvent(new Event('frcmob:session-change'));
  });
  await page.getByRole('button', { name: 'Export recording', exact: true }).click();
  await page.waitForFunction(() => window.__nativeCalls.some(call => call.plugin === 'Share' && call.method === 'share'));
  const write = await page.evaluate(() => window.__nativeCalls.find(call => call.plugin === 'Filesystem' && call.method === 'writeFile'));
  assert.equal(JSON.parse(write.options.data).recording.payload.pointsByTeam.frc254.length, 1);
  await page.evaluate(() => window.__nativeListeners.appUrlOpen({ url: 'frcmob://open#/privacy' }));
  await page.getByRole('heading', { name: /Privacy/i }).first().waitFor();
  await page.locator('a[target="_blank"]').first().click();
  await page.waitForFunction(() => window.__nativeCalls.some(call => call.plugin === 'Browser' && call.method === 'open'));
  await page.evaluate(() => location.hash = '/settings');
  await page.getByText('Match alerts are not available in this app build yet.', { exact: true }).waitFor({ state: 'attached' });
  assert.equal(await page.getByRole('button', { name: 'Enable match alerts', exact: true }).count(), 0);
  await page.goto(`${base}/#/scouting/record`);
  await page.getByText('On-Device Match Breakdown', { exact: true }).waitFor();
  assert.equal(new URL(page.url()).pathname, '/', 'Native recorder attempted a web-only document redirect');
  await page.waitForFunction(() => Boolean(window.__nativeListeners.backButton));
  assert.ok(await page.evaluate(() => document.documentElement.classList.contains('native-app')));
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 2));
  assert.ok(apiRequests.some(url => url.startsWith('https://scouting-app-iryg.vercel.app/api/')), 'Native API uses the app-local origin');
  await page.screenshot({ path: '/tmp/frcmob-native-recorder-fixture.png', fullPage: true });
  await page.evaluate(() => window.__nativeListeners.backButton({ canGoBack: false }));
  await page.waitForURL('**/#/');
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ passed: true, publicNativeApi: true, nativeShareExport: true, appLinks: true, externalBrowser: true, androidBack: true, recorderStaysInApp: true, noServiceWorker: true, phoneOverflow: false, pageErrors: errors }));
} finally { await browser.close(); }
