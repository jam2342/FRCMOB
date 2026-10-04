// Run against a production preview: npm run build, npm run preview -- --port 4179.
// Uses only local fixtures; never uploads to a real backend.
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { chromium } from 'playwright';

const base = process.env.OFFLINE_TEST_URL || 'http://127.0.0.1:4179';
assert.ok(['localhost', '127.0.0.1'].includes(new URL(base).hostname), 'Use a local preview, never production.');
const browser = await chromium.launch({ channel: 'chrome' });
try {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, reducedMotion: 'reduce', acceptDownloads: true });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', (error) => { errors.push(error.message); console.error(error.stack || error.message); });
  let pitPosts = 0;
  await context.route('**/*', async (route) => {
    const url = new URL(route.request().url());
    // Vite preview has no Vercel analytics endpoint; otherwise its SPA fallback
    // serves HTML as JavaScript and creates unrelated syntax errors.
    if (url.pathname.startsWith('/_vercel/')) return route.fulfill({ contentType: 'application/javascript', body: '' });
    if (url.pathname.endsWith('/health')) return route.fulfill({ json: { ok: true } });
    if (url.pathname.includes('/api/')) {
      if (url.pathname.endsWith('/pit-scouting') && route.request().method() === 'POST') {
        pitPosts++;
        return route.fulfill({ status: 409, json: { detail: 'Fixture conflict' } });
      }
      return route.fulfill({ json: url.pathname.endsWith('/health') ? { ok: true } : {} });
    }
    if (url.origin !== new URL(base).origin) return route.abort();
    return route.continue();
  });
  await page.addInitScript(() => {
    window.__offlineTestErrors = [];
    window.addEventListener('error', e => window.__offlineTestErrors.push({ message: e.message, file: e.filename, line: e.lineno, column: e.colno }));
    localStorage.setItem('frcmob_tour_prompt_dismissed_v1', '1');
    localStorage.setItem('scouting_tutorial_autoplay', 'false');
    if (localStorage.getItem('offline-test-seeded')) return;
    localStorage.setItem('offline-test-seeded', 'yes');
    localStorage.setItem('frcmob_offline_queue_v1', JSON.stringify([{
      id: 'offline-test-entry', queuedAt: new Date().toISOString(), method: 'POST',
      url: '/api/pit-scouting', body: JSON.stringify({ event_key: '2026test', team_key: 'frc254', payload: { notes: 'Recovered scout notes' } }),
      headers: { 'X-Workspace-Access': 'fixture-private-token' }, attempts: 0, label: 'Pit notes',
    }]));
  });
  await page.goto(`${base}/#/my-team`);
  await page.getByText('Saved on this phone', { exact: true }).waitFor();
  console.log('Recovery card loaded');
  await page.locator('section').filter({ hasText: 'Saved on this phone' }).getByRole('button', { name: 'Sync now', exact: true }).click();
  const recovery = page.getByRole('button', { name: 'Export unsynced changes' });
  await recovery.waitFor();
  console.log('Conflict retained');
  assert.equal(pitPosts, 1);
  await page.reload();
  await recovery.waitFor();
  await page.locator('section').filter({ hasText: 'Saved on this phone' }).getByRole('button', { name: 'Sync now', exact: true }).click();
  assert.equal(pitPosts, 1, 'Rejected writes must not replay automatically');
  const [download] = await Promise.all([page.waitForEvent('download'), recovery.click()]);
  const exported = await readFile(await download.path(), 'utf8');
  assert.match(exported, /Recovered scout notes/);
  assert.ok(!exported.includes('fixture-private-token'));
  assert.equal(JSON.parse(exported).changes.length, 1);
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 2), 'Recovery UI overflows phone width');
  await page.screenshot({ path: '/tmp/frcmob-offline-recovery.png', fullPage: true });

  await page.getByRole('button', { name: 'Get ready for offline', exact: true }).click();
  await page.locator('.offline-ready').getByText('Saved on this phone', { exact: true }).waitFor({ timeout: 60_000 });
  console.log('Offline preparation verified');

  // Warm the recorder route, then reopen it offline with a previously unseen query.
  console.log('Recovery export passed');
  await page.goto(`${base}/record.html#/scouting/record`);
  await page.getByText('On-Device Match Breakdown', { exact: true }).waitFor();
  await page.evaluate(() => navigator.serviceWorker.ready);
  await page.waitForFunction(() => Boolean(navigator.serviceWorker.controller));
  console.log('Recorder warmed', errors);
  await context.setOffline(true);
  await page.goto(`${base}/record.html?offline-cold-start=1#/scouting/record`);
  await page.getByText('On-Device Match Breakdown', { exact: true }).waitFor();
  assert.equal(await page.evaluate(() => crossOriginIsolated), true, 'Offline recorder lost isolation headers');
  await page.screenshot({ path: '/tmp/frcmob-offline-recorder.png', fullPage: true });
  if (errors.length) console.log(JSON.stringify(await page.evaluate(() => window.__offlineTestErrors)));
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ passed: true, conflictRetainedAfterReload: true, recoveryExportVerified: true, noCredentialsInExport: true, phoneOverflow: false, offlineRecorderIsolated: true, pageErrors: errors }));
} finally {
  await browser.close();
}
