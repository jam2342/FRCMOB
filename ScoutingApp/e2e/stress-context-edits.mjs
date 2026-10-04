// Independent UI regressions: every API request and mutation stays in disposable fixtures.
import assert from 'node:assert/strict';
import { chromium, webkit } from 'playwright';
import { mkdir, writeFile } from 'node:fs/promises';
const base = 'http://127.0.0.1:4179';
const engine = process.env.CONTEXT_BROWSER || 'chrome';
const out = process.env.CONTEXT_OUTPUT || `/tmp/frcmob-independent-stress/${engine}`;
await mkdir(out, { recursive: true });
const browser = await (engine === 'webkit' ? webkit : chromium).launch(engine === 'webkit' ? {} : { channel: 'chrome' });
const results = [], errors = [], requests = [];
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; };
const makeDoc = (id, title, event = '2026test', numbers = [254, 1678]) => ({ id, title, event_key: event, version: 1, live_mode: false, archived: false, created_by: null, created_at: null, updated_at: null, slots: numbers.map(n => ({ team_key: `frc${n}`, notes: '', dnp_reason: '', tier: 'first', status: 'available', picked_by_alliance: null })) });
const teamNumbers = event => event === '2026fast' ? [118, 971] : [254, 1678];
async function waitFor(condition, message) {
  for (let i = 0; i < 100; i++) { if (await condition()) return; await pause(50); }
  assert.fail(message);
}
async function check(page, label, fn) {
  try { await fn(); results.push({ label, passed: true }); console.log('PASS', label); }
  catch (error) { results.push({ label, passed: false, error: error.message }); await page.screenshot({ path: `${out}/${label}.png`, fullPage: true }); throw error; }
}
try {
  for (const width of [390, 1440]) {
    const context = await browser.newContext({ viewport: { width, height: 900 }, serviceWorkers: 'block' });
    const docs = new Map([[1, makeDoc(1, 'Saturday')], [2, makeDoc(2, 'Sunday')], [3, makeDoc(3, 'Slow event', '2026slow')], [4, makeDoc(4, 'Fast event', '2026fast', [118, 971])]]);
    let writeMode = 'normal', gate = null, slowLoad = null, readFailures = 0, activeWrites = 0, maxWrites = 0;
    const saves = [];
    const workspace = { id: 901, name: 'Local Scouts', frc_team_number: 254 }, me = { id: 901, display_name: 'Local Scout', role: 'leader' };
    await context.addInitScript(({ workspace, me }) => {
      localStorage.setItem('frcmob_tour_prompt_dismissed_v1', '1');
      localStorage.setItem('scouting_tutorial_autoplay', 'false');
      if (!localStorage.getItem('fixture-seeded')) {
        localStorage.setItem('fixture-seeded', '1');
        localStorage.setItem('frcmob_workspace_session_v1', JSON.stringify({ token: 'disposable-local-access', expiresAt: Date.now() / 1000 + 3600, workspace, me }));
      }
    }, { workspace, me });
    const routeFixture = async route => {
      const req = route.request(), url = new URL(req.url()), path = url.pathname.replace(/^\/api/, '');
      if (url.pathname.startsWith('/_vercel/')) return route.fulfill({ contentType: 'application/javascript', body: '' });
      const api = url.pathname.startsWith('/api/') || url.hostname === 'localhost' && url.port === '8000';
      if (!api) return url.origin === base ? route.continue() : route.abort();
      requests.push({ width, method: req.method(), path });
      if (path === '/health') return route.fulfill({ json: { ok: true } });
      if (path === '/workspaces/me') return route.fulfill({ json: { ok: true, workspace, me, members: [me] } });
      if (path === '/picklists' && req.method() === 'GET') {
        if (readFailures) { readFailures--; return route.fulfill({ status: 503, json: { detail: 'Fixture list unavailable' } }); }
        const event = url.searchParams.get('event_key');
        if (event === '2026slow' && slowLoad) await slowLoad.promise;
        return route.fulfill({ json: { ok: true, picklists: [...docs.values()].filter(d => d.event_key === event) } });
      }
      if (/^\/picklists\/\d+$/.test(path)) {
        const id = Number(path.split('/').pop());
        if (req.method() === 'GET') return route.fulfill({ json: { ok: true, picklist: docs.get(id) } });
        if (req.method() === 'PUT') {
          const body = req.postDataJSON(), mode = writeMode, capturedGate = gate;
          saves.push({ id, ...body }); activeWrites++; maxWrites = Math.max(maxWrites, activeWrites);
          if (capturedGate) { gate = null; await capturedGate.promise; }
          activeWrites--;
          if (mode === 'fail') return route.fulfill({ status: 503, json: { detail: 'Fixture save disconnected' } });
          if (mode === 'conflict') { writeMode = 'normal'; const shared = { ...docs.get(id), version: docs.get(id).version + 1, slots: docs.get(id).slots.map(s => ({ ...s, notes: 'Another scout edit' })) }; docs.set(id, shared); return route.fulfill({ json: { ok: false, conflict: true, picklist: shared } }); }
          if (body.version !== docs.get(id).version) return route.fulfill({ json: { ok: false, conflict: true, picklist: docs.get(id) } });
          const saved = { ...docs.get(id), ...body, version: body.version + 1 }; docs.set(id, saved);
          return route.fulfill({ json: { ok: true, picklist: saved } });
        }
      }
      if (path.startsWith('/teams/event/')) {
        const event = path.split('/')[3]; if (event === '2026slow' && slowLoad) await slowLoad.promise;
        return route.fulfill({ json: { teams: teamNumbers(event).map(n => ({ team_key: `frc${n}`, team_number: n, nickname: `Local ${n}` })) } });
      }
      if (path === '/pit-scouting') return route.fulfill({ json: { entries: [] } });
      if (path.endsWith('/schedule')) {
        const event = path.split('/')[3]; if (event === '2026slow' && slowLoad) await slowLoad.promise;
        return route.fulfill({ json: { event_key: event, matches: [1, 2].map(i => ({ match_key: `${event}_qm${i}`, red: (i === 1 ? [254, 1678, 118] : [1114, 2056, 1690]).map(n => ({ team_key: `frc${n}` })), blue: [971, 2910, 868].map(n => ({ team_key: `frc${n}` })) })) } });
      }
      return route.fulfill({ status: 404, json: { detail: 'No local fixture' } });
    };
    await context.route('**/*', routeFixture);
    const page = await context.newPage(); page.on('pageerror', error => { errors.push({ message: error.message, stack: error.stack, route: page.url() }); console.error('PAGEERROR', error.stack, page.url()); });
    page.on('requestfailed', req => { if (req.url().startsWith(base)) console.log('REQUESTFAILED', new URL(req.url()).pathname, req.failure()?.errorText); });
    const go = route => page.goto(`${base}/#${route}`);
    const notes = page.getByRole('textbox', { name: 'Notes', exact: true });
    const openNotes = async () => { if (!await notes.count()) await page.getByRole('button', { name: /#254/ }).click(); await notes.waitFor(); };
    await go('/compare/picklist?event=2026test'); await openNotes();
    await check(page, `slow-save-newer-edit-${width}`, async () => {
      gate = deferred(); const held = gate; const count = saves.length;
      await notes.fill('Submitted snapshot'); await waitFor(() => saves.length === count + 1, 'First save never started');
      await notes.fill('Newer edit during save'); held.resolve();
      await waitFor(() => saves.length === count + 2 && docs.get(1).slots[0].notes === 'Newer edit during save', 'Newer edit was not shared');
      assert.equal(maxWrites, 1); assert.equal(saves[count + 1].version, saves[count].version + 1);
      await page.screenshot({ path: `${out}/picklist-after-${width}.png`, fullPage: true });
    });
    await check(page, `switch-before-save-${width}`, async () => {
      await notes.fill('Keep edit across list switch'); await page.getByLabel('Select picklist').selectOption('2');
      await waitFor(() => docs.get(1).slots[0].notes === 'Keep edit across list switch', 'Switched list edit was lost');
      await page.getByLabel('Select picklist').selectOption('1'); assert.equal(await notes.inputValue(), 'Keep edit across list switch');
    });
    await check(page, `failed-save-reload-retry-${width}`, async () => {
      writeMode = 'fail'; await notes.fill('Recover after failed save'); await page.getByText(/Fixture save disconnected/).waitFor();
      await page.reload(); await openNotes(); assert.equal(await notes.inputValue(), 'Recover after failed save');
      writeMode = 'normal'; await page.getByRole('button', { name: 'Retry saving' }).click();
      await waitFor(() => docs.get(1).slots[0].notes === 'Recover after failed save', 'Retry did not share recovered draft');
    });
    await check(page, `route-back-pending-draft-${width}`, async () => {
      writeMode = 'fail';
      await notes.fill('Draft before accidental navigation'); await go('/settings'); await page.getByRole('combobox', { name: 'Theme', exact: true }).waitFor();
      await page.goBack(); await openNotes(); assert.equal(await notes.inputValue(), 'Draft before accidental navigation');
      writeMode = 'normal';
      await page.getByRole('button', { name: 'Retry saving' }).click(); await waitFor(() => docs.get(1).slots[0].notes === 'Draft before accidental navigation', 'Recovered route draft did not save');
    });
    await check(page, `conflict-preserves-local-choice-${width}`, async () => {
      writeMode = 'conflict'; await notes.fill('My conflict edit'); await page.getByRole('button', { name: 'Save my changes' }).waitFor();
      assert.equal(await notes.inputValue(), 'My conflict edit'); const count = saves.length;
      await notes.fill('My revised conflict edit'); await pause(1100); assert.equal(saves.length, count);
      await page.getByRole('button', { name: 'Save my changes' }).click(); await waitFor(() => docs.get(1).slots[0].notes === 'My revised conflict edit', 'Explicit conflict choice did not save');
    });
    await check(page, `read-failure-same-event-retry-${width}`, async () => {
      readFailures = 1; await page.reload(); await page.getByText('Fixture list unavailable', { exact: true }).waitFor();
      await page.getByRole('button', { name: 'Load Event', exact: true }).click(); await openNotes();
      assert.equal(await notes.inputValue(), 'My revised conflict edit');
    });
    await check(page, `out-of-order-event-${width}`, async () => {
      slowLoad = deferred(); const held = slowLoad;
      const started = page.waitForRequest(r => r.url().includes('/picklists?') && r.url().includes('2026slow'));
      await page.evaluate(() => { location.hash = '/compare/picklist?event=2026slow'; }); await started;
      await page.evaluate(() => { location.hash = '/compare/picklist?event=2026fast'; });
      await page.getByRole('heading', { name: 'Fast event', exact: true }).waitFor(); held.resolve(); slowLoad = null; await pause(200);
      assert.equal(await page.getByLabel('Select picklist').inputValue(), '4');
      assert.equal(await page.getByRole('heading', { name: 'Slow event', exact: true }).count(), 0);
    });
    await check(page, `quota-route-recovery-${width}`, async () => {
      await go('/compare/picklist?event=2026test'); await openNotes();
      await page.evaluate(() => { const original = Storage.prototype.setItem; Storage.prototype.setItem = function(k, v) { if (k.startsWith('frcmob_picklist_draft')) throw new DOMException('Full', 'QuotaExceededError'); return original.call(this, k, v); }; });
      writeMode = 'fail';
      await notes.fill('Memory-only draft'); await page.getByText(/Device storage is full/).waitFor();
      await go('/settings'); await page.getByRole('combobox', { name: 'Theme', exact: true }).waitFor(); await page.goBack(); await openNotes();
      assert.equal(await notes.inputValue(), 'Memory-only draft');
      writeMode = 'normal';
      await page.getByRole('button', { name: 'Retry saving' }).click(); await waitFor(() => docs.get(1).slots[0].notes === 'Memory-only draft', 'Memory-only recovery did not share');
    });
    await check(page, `recorder-changing-keys-${width}`, async () => {
      await go('/scouting/record'); const input = page.getByRole('textbox', { name: 'Match key', exact: true }); await input.waitFor();
      await input.fill('2026test_qm1'); await page.getByRole('button', { name: 'Load match teams' }).click(); await page.getByRole('button', { name: 'Continue to calibration' }).waitFor();
      await input.fill('2026test_qm2'); assert.equal(await page.getByRole('button', { name: 'Continue to calibration' }).count(), 0);
      await page.getByRole('button', { name: 'Load match teams' }).click(); await page.getByRole('button', { name: 'Continue to calibration' }).waitFor();
      assert.match(await page.locator('.odr-teams').innerText(), /1114/); assert.doesNotMatch(await page.locator('.odr-teams').innerText(), /254/);
      await page.getByRole('textbox', { name: 'Event key (optional)' }).fill('2026other'); assert.equal(await page.getByRole('button', { name: 'Continue to calibration' }).count(), 0);
      await page.getByRole('button', { name: 'Load match teams' }).click(); await page.getByRole('alert').filter({ hasText: 'event key must match' }).waitFor();
      await page.screenshot({ path: `${out}/recorder-after-${width}.png`, fullPage: true });
    });
    await check(page, `recorder-stale-load-${width}`, async () => {
      const input = page.getByRole('textbox', { name: 'Match key', exact: true });
      await page.getByRole('textbox', { name: 'Event key (optional)' }).fill('');
      slowLoad = deferred(); const held = slowLoad; const started = page.waitForRequest(r => r.url().includes('/2026slow/schedule'));
      await input.fill('2026slow_qm1'); await page.getByRole('button', { name: 'Load match teams' }).click(); await started;
      await input.fill('2026fast_qm2'); held.resolve(); slowLoad = null; await pause(200);
      assert.equal(await page.getByRole('button', { name: 'Continue to calibration' }).count(), 0);
      await page.getByRole('button', { name: 'Load match teams' }).click(); await page.getByRole('button', { name: 'Continue to calibration' }).waitFor();
      assert.match(await page.locator('.odr-teams').innerText(), /1114/);
    });
    await check(page, `recorder-invalid-paste-${width}`, async () => {
      const count = requests.filter(r => r.path.endsWith('/schedule')).length;
      await page.getByRole('textbox', { name: 'Match key', exact: true }).fill('https://example.test/invalid'); await page.getByRole('button', { name: 'Load match teams' }).click();
      await page.getByRole('alert').filter({ hasText: 'Enter a match key' }).waitFor(); assert.equal(requests.filter(r => r.path.endsWith('/schedule')).length, count);
    });
    await check(page, `workspace-switch-private-draft-${width}`, async () => {
      await go('/compare/picklist?event=2026test'); await openNotes(); writeMode = 'fail'; await notes.fill('Private old-workspace draft');
      await page.evaluate(() => { const session = JSON.parse(localStorage.getItem('frcmob_workspace_session_v1')); session.workspace.id = 902; session.token = 'other-disposable-access'; const value = JSON.stringify(session); localStorage.setItem('frcmob_workspace_session_v1', value); window.dispatchEvent(new StorageEvent('storage', { key: 'frcmob_workspace_session_v1', newValue: value })); });
      await page.waitForTimeout(100); await openNotes(); assert.notEqual(await notes.inputValue(), 'Private old-workspace draft');
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 2));
    });
    // Use a fresh document/context so previous navigation cancellation cannot
    // short-circuit shell preparation before the quota fixture is reached.
    const quotaContext = await browser.newContext({ viewport: { width, height: 900 }, serviceWorkers: 'block' });
    await quotaContext.route('**/*', routeFixture);
    await quotaContext.addInitScript(() => {
      localStorage.setItem('frcmob_tour_prompt_dismissed_v1', '1');
      window.__quotaHits = 0;
      window.__originalCachePut = Cache.prototype.put;
      Cache.prototype.put = function() {
        window.__quotaHits++;
        return Promise.reject(new DOMException('Fixture cache full', 'QuotaExceededError'));
      };
    });
    const quotaPage = await quotaContext.newPage();
    quotaPage.on('pageerror', error => errors.push({ message: error.message, stack: error.stack, route: quotaPage.url() }));
    await check(quotaPage, `recorder-cache-quota-recovery-${width}`, async () => {
      await quotaPage.goto(`${base}/record.html#/scouting/record`);
      await quotaPage.getByText('Device storage is full. Free up space, then retry saving the detector.', { exact: true }).waitFor();
      assert.ok(await quotaPage.evaluate(() => window.__quotaHits > 0), 'Quota fixture was not reached');
      assert.ok(await quotaPage.getByRole('textbox', { name: 'Match key', exact: true }).isEnabled());
      await quotaPage.screenshot({ path: `${out}/recorder-cache-recovery-${width}.png`, fullPage: true });
      await quotaPage.evaluate(() => { Cache.prototype.put = window.__originalCachePut; });
      await quotaPage.getByRole('button', { name: 'Retry saving detector' }).click();
      await quotaPage.getByText('Detector saved on this phone: works with no signal.', { exact: true }).waitFor();
      assert.ok(await quotaPage.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 2));
    });
    await quotaContext.close();
    await context.close();
  }
  assert.deepEqual(errors, []);
} catch (error) { console.error(error); process.exitCode = 1; }
finally {
  await writeFile(`${out}/report.json`, JSON.stringify({ engine, results, errors, requests, productionWrites: 0 }, null, 2));
  console.log(JSON.stringify({ engine, checks: results.length, failures: results.filter(r => !r.passed).length, errors, productionWrites: 0 }));
  await browser.close();
}
