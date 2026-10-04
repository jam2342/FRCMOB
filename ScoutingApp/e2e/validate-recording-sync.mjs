import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { chromium } from 'playwright';
const base = process.env.OFFLINE_TEST_URL || 'http://127.0.0.1:4179';
assert.ok(['localhost', '127.0.0.1'].includes(new URL(base).hostname));
const browser = await chromium.launch({ channel: 'chrome' });
try {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, serviceWorkers: 'block', acceptDownloads: true });
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.pathname.startsWith('/_vercel/')) return route.fulfill({ contentType: 'application/javascript', body: '' });
    if (url.pathname.includes('/api/') || url.pathname.endsWith('/health')) return route.fulfill({ json: { ok: true } });
    if (url.origin !== new URL(base).origin) return route.abort();
    return route.continue();
  });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(`${base}/#/my-team`);
  await page.evaluate(async () => {
    localStorage.setItem('frcmob_tour_prompt_dismissed_v1', '1');
    const db = await new Promise((resolve, reject) => {
      const request = indexedDB.open('frcmob-ondevice', 1);
      request.onupgradeneeded = () => {
        for (const name of ['sessions', 'calibrations']) if (!request.result.objectStoreNames.contains(name)) request.result.createObjectStore(name, { keyPath: 'id' });
      };
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
    const tx = db.transaction('sessions', 'readwrite');
    tx.objectStore('sessions').put({ id: 'fixture-recording', matchKey: '2026test_qm1', eventKey: '2026test', createdAt: 1, synced: false, workspaceId: 1, workspaceName: 'Recovery Team', syncFailure: { kind: 'rejected', status: 409, at: 1 }, payload: { pointsByTeam: { frc254: [{ t: 1, x: 2, y: 3 }] } }, token: 'fixture-private-token' });
    await new Promise((resolve, reject) => { tx.oncomplete = resolve; tx.onerror = () => reject(tx.error); });
    db.close();
  });
  await page.reload();
  await page.getByText('Join your team to upload this recording.', { exact: true }).waitFor();
  await page.getByText(/server response 409/).waitFor();
  const [download] = await Promise.all([page.waitForEvent('download'), page.getByRole('button', { name: 'Export recording', exact: true }).click()]);
  const exported = await readFile(await download.path(), 'utf8');
  assert.equal(JSON.parse(exported).recording.payload.pointsByTeam.frc254.length, 1);
  assert.ok(!exported.includes('fixture-private-token'));
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 2));
  await page.screenshot({ path: '/tmp/frcmob-recording-recovery.png', fullPage: true });
  await page.reload();
  await page.getByText(/server response 409/).waitFor();
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ passed: true, recordingRetainedAfterReload: true, exportContainsTracks: true, noCredentialsInExport: true, phoneOverflow: false, pageErrors: errors }));
} finally { await browser.close(); }
