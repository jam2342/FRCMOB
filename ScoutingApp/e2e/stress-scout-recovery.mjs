// Manual scouting recovery regressions. All API calls and writes use local fixtures.
import assert from 'node:assert/strict';
import { chromium, webkit } from 'playwright';
import { mkdir, writeFile } from 'node:fs/promises';
const base = process.env.SCOUT_TEST_URL || 'http://127.0.0.1:4179';
assert.ok(['localhost', '127.0.0.1'].includes(new URL(base).hostname));
const out = process.env.SCOUT_TEST_OUTPUT || '/tmp/frcmob-scout-recovery';
await mkdir(out, { recursive: true });
const engine = process.env.SCOUT_TEST_BROWSER === 'webkit' ? 'webkit' : 'chrome';
const browser = await (engine === 'webkit' ? webkit : chromium).launch(engine === 'chrome' ? { channel: 'chrome' } : {});
const profiles = [
  { name: 'small-dark', width: 320, height: 720, theme: 'dark' },
  { name: 'phone-light', width: 390, height: 844, theme: 'light' },
  { name: 'landscape-dark', width: 844, height: 390, theme: 'dark' },
  { name: 'tablet-light', width: 768, height: 1024, theme: 'light' },
  { name: 'desktop-dark', width: 1440, height: 900, theme: 'dark' },
  { name: 'desktop-light', width: 1440, height: 900, theme: 'light' },
  { name: 'large-text', width: 390, height: 844, theme: 'dark', largeText: true },
];
const selected = process.env.SCOUT_TEST_PROFILES ? profiles.filter(p => process.env.SCOUT_TEST_PROFILES.split(',').includes(p.name)) : profiles;
assert.ok(selected.length);
const results = [], errors = [], writes = [];
const workspace = { id: 9002, name: 'Disposable scout workspace', frc_team_number: 254 };
const session = { token: 'local-scout-fixture', expiresAt: Date.now() / 1000 + 3600, workspace, me: { id: 9002, display_name: 'Fixture Scout', role: 'member' } };
const scoutRoute = '/scouting?event=2026test&match=2026test_qm1&team=frc254';
const draftKey = 'frcmob_scout_draft_v1:w9002:2026test_qm1:frc254';
const entriesKey = 'scouting_manual_entries_v2:w9002';
async function check(page, profile, label, verify) {
  try { await verify(); results.push({ profile, label, passed: true }); }
  catch (error) {
    results.push({ profile, label, passed: false, error: error.message });
    await page.screenshot({ path: `${out}/${profile}-${label}-failed.png`, fullPage: true });
    throw error;
  }
}
try {
  for (const profile of selected) {
    const context = await browser.newContext({ viewport: { width: profile.width, height: profile.height }, serviceWorkers: 'block', reducedMotion: 'reduce' });
    await context.addInitScript(({ session, theme }) => {
      localStorage.setItem('frcmob_tour_prompt_dismissed_v1', '1');
      localStorage.setItem('scouting_tutorial_autoplay', 'false');
      localStorage.setItem('scouting_theme_mode', theme);
      if (!localStorage.getItem('frcmob_workspace_session_v1')) localStorage.setItem('frcmob_workspace_session_v1', JSON.stringify(session));
    }, { session, theme: profile.theme });
    await context.route('**/*', async route => {
      const request = route.request(), url = new URL(request.url());
      if (url.pathname.startsWith('/_vercel/')) return route.fulfill({ contentType: 'application/javascript', body: '' });
      const api = url.pathname.startsWith('/api/') || (url.hostname === 'localhost' && url.port === '8000');
      if (!api) return url.origin === new URL(base).origin && request.method() === 'GET' ? route.continue() : route.abort();
      const path = url.pathname.replace(/^\/api/, '');
      if (request.method() !== 'GET') { writes.push({ path, method: request.method() }); return route.fulfill({ status: 409, json: { detail: 'Mutation blocked by local fixture' } }); }
      if (path === '/health') return route.fulfill({ json: { ok: true } });
      if (path === '/workspaces/me') return route.fulfill({ json: { ok: true, workspace, me: session.me, members: [session.me] } });
      if (path === '/events/suggested' || path === '/events/search') return route.fulfill({ json: { ok: true, events: [{ key: '2026test', name: 'Local scout event', year: 2026, start_date: '2026-10-01', end_date: '2026-10-03' }] } });
      if (path.includes('/schedule')) return route.fulfill({ json: {
        ok: true, event_key: '2026test', event_name: 'Local scout event', published: true, times_published: false, count: 2,
        matches: [1, 2].map(n => ({ match_key: `2026test_qm${n}`, display_name: `QM ${n}`, comp_level: 'qm', match_number: n, set_number: 1, scheduled_time: null, has_time: false,
          red: [254, 1678, 1323].map((n, i) => ({ team_key: 'frc' + n, team_number: n, nickname: 'Fixture robot', station: 'Red ' + (i + 1) })),
          blue: [1114, 2056, 118].map((n, i) => ({ team_key: 'frc' + n, team_number: n, nickname: 'Fixture robot', station: 'Blue ' + (i + 1) })),
        })),
      } });
      if (path.startsWith('/teams/event/')) return route.fulfill({ json: { ok: true, teams: [] } });
      if (path.startsWith('/teams/')) return route.fulfill({ json: {} });
      return route.fulfill({ status: 404, json: { detail: 'No local fixture data' } });
    });
    const page = await context.newPage();
    page.on('pageerror', error => errors.push({ profile: profile.name, error: error.message }));
    page.on('dialog', dialog => dialog.dismiss());
    const navigate = async route => {
      await page.evaluate(route => { location.hash = route; }, route);
      if (route.startsWith('/scouting')) await showAuto();
    };
    const showAuto = async () => {
      await page.locator('.scouting-layout-grid').waitFor({ state: 'attached' });
      if (profile.width <= 1120) {
        if (await page.getByRole('tab', { name: 'Board', exact: true }).isVisible()) await page.getByRole('tab', { name: 'Board', exact: true }).click();
        await page.getByRole('tab', { name: 'Capture', exact: true }).click();
        await page.getByRole('tab', { name: 'Auto', exact: true }).click();
      }
      await page.getByLabel('Auto fuel scored', { exact: true }).waitFor();
    };
    const counter = page.getByLabel('Auto fuel scored', { exact: true });
    const save = () => profile.width <= 1120 ? page.getByRole('button', { name: 'Save', exact: true }) : page.getByRole('button', { name: 'Save Entry', exact: true });
    const currentEntries = () => page.evaluate(key => JSON.parse(localStorage.getItem(key) || '[]'), entriesKey);
    const setWorkspace = id => page.evaluate(({ id, session }) => {
      const key = 'frcmob_workspace_session_v1', oldValue = localStorage.getItem(key);
      const next = { ...session, workspace: { ...session.workspace, id }, token: `local-fixture-${id}` };
      const newValue = JSON.stringify(next);
      localStorage.setItem(key, newValue);
      window.dispatchEvent(new StorageEvent('storage', { key, oldValue, newValue, storageArea: localStorage }));
    }, { id, session });
    await page.goto(base + '/#' + scoutRoute); await showAuto();
    if (profile.largeText) await page.addStyleTag({ content: 'html { font-size: 24px !important; }' });
    if (profile.width > 1120) await check(page, profile.name, 'focused-setup-control-remains-reachable', async () => {
      const rapid = page.getByRole('tab', { name: 'Rapid Tap', exact: true });
      await rapid.evaluate(button => { button.focus(); button.scrollIntoView({ block: 'start' }); });
      await rapid.evaluate(async button => {
        for (let frame = 0; frame < 30; frame++) await new Promise(requestAnimationFrame);
        const r = button.getBoundingClientRect();
        const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
        if (hit !== button && !button.contains(hit)) throw new Error('Focused setup control is covered by fixed headers');
      });
      await rapid.click(); await page.getByRole('tab', { name: 'Match Scout', exact: true }).click();
    });
    await check(page, profile.name, 'rapid-navigation-keeps-final-edit', async () => {
      await counter.fill('7'); await navigate('/settings');
      await page.getByText('Workflow Defaults', { exact: true }).waitFor(); await navigate(scoutRoute);
      assert.equal(await counter.inputValue(), '7');
    });
    await check(page, profile.name, 'immediate-refresh-keeps-edit', async () => {
      await counter.fill('11'); await page.reload(); await showAuto(); assert.equal(await counter.inputValue(), '11');
    });
    await check(page, profile.name, 'robot-and-match-drafts-stay-separate', async () => {
      await navigate(scoutRoute.replace('team=frc254', 'team=frc1678')); assert.equal(await counter.inputValue(), '0');
      await counter.fill('21'); await navigate(scoutRoute); assert.equal(await counter.inputValue(), '11');
      await navigate(scoutRoute.replace('match=2026test_qm1', 'match=2026test_qm2')); assert.equal(await counter.inputValue(), '0');
      await navigate(scoutRoute); assert.equal(await counter.inputValue(), '11');
    });
    await check(page, profile.name, 'browser-back-forward-recovers', async () => {
      await navigate('/settings'); await page.getByText('Workflow Defaults', { exact: true }).waitFor();
      await page.goBack(); await showAuto(); assert.equal(await counter.inputValue(), '11');
      await page.goForward(); await page.getByText('Workflow Defaults', { exact: true }).waitFor(); await navigate(scoutRoute);
    });
    await check(page, profile.name, 'workspace-switch-clears-old-form', async () => {
      await setWorkspace(9003); await page.waitForFunction(() => document.querySelector('input[type=number]')?.value === '0');
      assert.equal(await counter.inputValue(), '0'); await counter.fill('33');
      await setWorkspace(9002); await page.waitForFunction(() => document.querySelector('input[type=number]')?.value === '11');
      assert.equal(await counter.inputValue(), '11');
    });
    await check(page, profile.name, 'invalid-counters-stay-bounded', async () => {
      await counter.fill('-12'); assert.equal(await counter.inputValue(), '0');
      await counter.fill('999999999'); assert.equal(await counter.inputValue(), '999');
      await counter.fill('17');
    });
    await page.getByRole('tab', { name: 'Setup', exact: true }).click();
    await page.getByRole('tab', { name: 'Rapid Tap', exact: true }).click();
    await showAuto();
    await page.evaluate(() => {
      const original = Storage.prototype.setItem;
      window.__scoutOriginalSetItem = original;
      window.__scoutQuota = true;
      Storage.prototype.setItem = function(key, value) {
        if (window.__scoutQuota) throw new DOMException('Injected full device storage', 'QuotaExceededError');
        return original.call(this, key, value);
      };
    });
    await counter.fill('18');
    await check(page, profile.name, 'header-settles-without-scroll-feedback', async () => {
      const positions = await save().evaluate(async button => {
        const positions = [];
        for (let frame = 0; frame < 60; frame++) {
          await new Promise(requestAnimationFrame);
          if (frame >= 40) positions.push(button.getBoundingClientRect().top);
        }
        return positions;
      });
      assert.ok(Math.max(...positions) - Math.min(...positions) <= 0.5, 'Save continues moving after the user stops scrolling');
    });
    await check(page, profile.name, 'failed-save-keeps-form-without-success', async () => {
      await save().click();
      await page.getByRole('alert').filter({ hasText: 'Device storage is full' }).waitFor();
      assert.equal(await counter.inputValue(), '18'); assert.equal((await currentEntries()).length, 0);
      assert.ok(!/Scouting failed to load|Saved FRC254/.test(await page.locator('body').innerText()));
    });
    await check(page, profile.name, 'volatile-draft-survives-route-change', async () => {
      await navigate('/settings'); await page.getByText('Workflow Defaults', { exact: true }).waitFor();
      await navigate(scoutRoute); assert.equal(await counter.inputValue(), '18');
      const guarded = await page.evaluate(() => { const e = new Event('beforeunload', { cancelable: true }); window.dispatchEvent(e); return e.defaultPrevented; });
      assert.equal(guarded, true);
    });
    await page.screenshot({ path: `${out}/${profile.name}-storage-recovery.png` });
    await check(page, profile.name, 'retry-persists-one-report', async () => {
      await page.evaluate(() => { window.__scoutQuota = false; });
      // Capture mode resets when the page remounts; reselect Rapid Tap after
      // the route-recovery check before asserting its post-save reset.
      await page.getByRole('tab', { name: 'Setup', exact: true }).click();
      await page.getByRole('tab', { name: 'Rapid Tap', exact: true }).click(); await showAuto(); await save().click();
      await page.waitForFunction(key => JSON.parse(localStorage.getItem(key) || '[]').length === 1, entriesKey);
      const entries = await currentEntries(); assert.equal(entries[0].form.auto_scored, 18); assert.equal(entries[0].team_key, 'frc254');
      await page.waitForFunction(id => document.getElementById(id)?.value === '0', await counter.getAttribute('id'));
      assert.equal(await counter.inputValue(), '0');
      assert.equal(entries[0].match_key, '2026test_qm1'); assert.ok(!await page.getByRole('alert').filter({ hasText: 'Device storage is full' }).count());
      assert.equal(await page.evaluate(key => localStorage.getItem(key), draftKey), null);
    });
    await check(page, profile.name, 'saved-report-survives-refresh', async () => {
      await page.reload(); await showAuto(); assert.equal((await currentEntries()).length, 1);
      assert.equal((await currentEntries())[0].form.auto_scored, 18);
    });
    await check(page, profile.name, 'rapid-tap-clears-notes-with-counters', async () => {
      await page.getByRole('tab', { name: 'Setup', exact: true }).click();
      await page.getByRole('tab', { name: 'Rapid Tap', exact: true }).click(); await showAuto();
      await counter.fill('21');
      if (profile.width <= 1120) await page.getByRole('tab', { name: 'Notes', exact: true }).click();
      const notes = page.getByLabel('Scouting notes', { exact: true });
      await notes.fill('Only applies to this report. '.repeat(40)); assert.equal((await notes.inputValue()).length, 600);
      await save().click(); await page.waitForFunction(() => document.querySelector('textarea[aria-label="Scouting notes"]')?.value === '');
      assert.equal(await notes.inputValue(), '');
      const entries = await currentEntries(); assert.equal(entries.length, 2); assert.equal(entries[0].notes.length, 600);
      assert.equal(entries[0].form.auto_scored, 21); await showAuto(); assert.equal(await counter.inputValue(), '0');
    });
    await check(page, profile.name, 'responsive-recovery-no-overflow-or-crash', async () => {
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 2));
      assert.ok(!/Scouting failed to load/.test(await page.locator('body').innerText()));
    });
    await page.screenshot({ path: `${out}/${profile.name}-verified.png`, fullPage: true });
    await context.close(); console.log('PASS', engine, profile.name);
  }
  assert.deepEqual(errors, []);
} finally {
  await writeFile(out + '/report.json', JSON.stringify({ engine, results, errors, interceptedWrites: writes, productionWrites: 0 }, null, 2));
  await browser.close();
}
console.log(JSON.stringify({ engine, passed: results.filter(r => r.passed).length, failed: results.filter(r => !r.passed).length, pageErrors: errors.length, productionWrites: 0 }));
