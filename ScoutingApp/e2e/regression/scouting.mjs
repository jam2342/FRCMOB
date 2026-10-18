// Scouting on a phone: saved reports must reach the team and never vanish.
// Each check is a bug the QA round of 2026-10-16 found (PR #181).
import { BASE, bodyText, check, phone, sleep, storedJson, until } from './lib.mjs';
import { makeServer } from './mocks/room.mjs';
import { installPit } from './mocks/pit.mjs';
import { installPick } from './mocks/picklist.mjs';

const BOARD = '/#/scouting?event=2026arc&match=2026arc_qm1&team=frc971';
const ROOM_ENTRIES = 'scouting_manual_entries_v2:w7';
const LOCAL_ENTRIES = 'scouting_manual_entries_v2';
const ANON_PROFILE = { scouting_manual_profile_v1: 'Alice' };

async function openBoard(ctx, path = BOARD) {
  const page = await ctx.newPage();
  page.on('dialog', (dialog) => dialog.accept().catch(() => {}));
  await page.goto(BASE + path);
  await page.getByRole('button', { name: /Increase TELEOP FUEL SCORED/i }).first().waitFor({ timeout: 30000 });
  return page;
}
async function score(page, taps) {
  for (let i = 0; i < taps; i += 1) await page.getByRole('button', { name: /Increase TELEOP FUEL SCORED/i }).first().tap();
}
const save = (page) => page.getByRole('button', { name: /^Save/ }).first().tap();

export default [
  {
    name: 'a room report saved just before the app closes reaches the team on the next open',
    async run(browser) {
      const ctx = await phone(browser);
      const server = makeServer();
      await server.install(ctx);
      const page = await openBoard(ctx);
      await score(page, 4);
      await save(page);
      await sleep(1500); // inside the 5 s Undo window: nothing sent yet
      check(server.entries.size === 0, 'report was sent before Undo closed');
      await page.close();
      const reopened = await ctx.newPage();
      await reopened.goto(`${BASE}/#/scouting?event=2026arc`);
      await until(() => server.entries.size === 1, { timeout: 30000, what: 'the report to reach the team room' });
    },
  },
  {
    name: 'a room report whose first send failed is sent again',
    async run(browser) {
      const ctx = await phone(browser);
      const server = makeServer();
      await server.install(ctx);
      let failing = true;
      await ctx.route('**/api/scouting/rooms/*/entries', (route) => {
        if (route.request().method() === 'POST' && failing) {
          return route.fulfill({ status: 503, contentType: 'application/json', body: '{"detail":"Service temporarily unavailable"}' });
        }
        return route.fallback();
      });
      const page = await openBoard(ctx);
      await score(page, 4);
      await save(page);
      await sleep(8000); // Undo closes, the send fails
      check(server.entries.size === 0, 'expected the first send to fail');
      failing = false;
      await until(() => server.entries.size === 1, { timeout: 50000, what: 'the retry to reach the team room' });
    },
  },
  {
    name: 'a correction survives a room snapshot during its Undo window',
    async run(browser) {
      const ctx = await phone(browser);
      const server = makeServer();
      await server.install(ctx);
      const page = await openBoard(ctx);
      await score(page, 2);
      await save(page);
      await until(() => server.entries.size === 1, { timeout: 15000, what: 'the first version to be shared' });
      await score(page, 3);
      await save(page);
      await sleep(800);
      const corrected = (await storedJson(page, ROOM_ENTRIES))[0]?.form?.teleop_scored;
      server.writesFail = true; // and the correction's own send will fail
      server.snapshotAll();
      await sleep(8000);
      await page.reload();
      await sleep(5000);
      const kept = (await storedJson(page, ROOM_ENTRIES))[0]?.form?.teleop_scored;
      check(kept === corrected, `the correction was reverted (kept ${kept}, saved ${corrected})`);
    },
  },
  {
    name: 'two tabs saving different robots keep both reports',
    async run(browser) {
      const ctx = await phone(browser, { storage: ANON_PROFILE });
      const first = await openBoard(ctx);
      const second = await openBoard(ctx, '/#/scouting?event=2026arc&match=2026arc_qm2&team=frc167');
      await first.bringToFront();
      await score(first, 1);
      await save(first);
      await sleep(6000);
      await second.bringToFront();
      await score(second, 2);
      await save(second);
      await sleep(6000);
      await first.reload();
      await sleep(3000);
      const kept = (await storedJson(first, LOCAL_ENTRIES)).map((entry) => entry.match_key);
      check(kept.includes('2026arc_qm1') && kept.includes('2026arc_qm2'), `a report was lost: ${JSON.stringify(kept)}`);
    },
  },
  {
    name: 'a double tap on Save does not ask to replace the report with itself',
    async run(browser) {
      const ctx = await phone(browser);
      const server = makeServer();
      await server.install(ctx);
      const page = await openBoard(ctx);
      const dialogs = [];
      page.removeAllListeners('dialog');
      page.on('dialog', (dialog) => { dialogs.push(dialog.message()); dialog.dismiss().catch(() => {}); });
      await score(page, 3);
      await save(page);
      await sleep(120);
      await save(page);
      await sleep(600);
      check(dialogs.length === 0, `asked: ${dialogs.join(' / ')}`);
      check(/Already saved/.test(await bodyText(page)), 'no "Already saved" message');
    },
  },
  {
    name: 'Clear All during the Undo window stays cleared',
    async run(browser) {
      const ctx = await phone(browser, { storage: ANON_PROFILE });
      const page = await openBoard(ctx);
      await score(page, 1);
      await save(page);
      await sleep(300);
      await page.getByText('Saved', { exact: true }).first().tap();
      await page.getByRole('button', { name: /Clear All/ }).first().tap();
      await sleep(8000);
      const left = await storedJson(page, LOCAL_ENTRIES);
      check(left.length === 0, `the cleared report came back: ${left.length}`);
    },
  },
  {
    name: 'a picklist move is shared even when leaving the page at once',
    async run(browser) {
      const ctx = await phone(browser);
      const server = makeServer();
      installPit(server);
      installPick(server);
      await server.install(ctx);
      const page = await ctx.newPage();
      await page.goto(`${BASE}/#/compare/picklist?event=2026arc`);
      const move = page.getByRole('button', { name: 'Move frc971 down' });
      await move.waitFor({ timeout: 30000 });
      await move.tap();
      await page.evaluate(() => { location.hash = '#/'; });
      await until(() => server.puts.length >= 1, { timeout: 15000, what: 'the move to be sent' });
      check(server.pick.slots[1].team_key === 'frc971', 'the server did not get the new order');
    },
  },
  {
    name: "a pit draft delivered while the page was closed doesn't hide a teammate's newer notes",
    async run(browser) {
      const ctx = await phone(browser);
      const server = makeServer();
      installPit(server);
      await server.install(ctx);
      const page = await ctx.newPage();
      page.on('dialog', (dialog) => dialog.accept().catch(() => {}));
      await page.goto(`${BASE}/#/scouting/pit?event=2026arc`);
      await page.getByRole('button', { name: /^Team 971\b/ }).waitFor({ timeout: 30000 });
      await page.getByRole('button', { name: /^Team 971\b/ }).tap();
      await page.locator('textarea').first().fill('Mine: swerve');
      await server.setOffline(ctx, true);
      await page.getByRole('button', { name: /^Save entry$/ }).first().tap();
      await sleep(1500);
      await page.goto(`${BASE}/#/`);
      await server.setOffline(ctx, false);
      await until(() => server.pitById.has('2026arc|frc971'), { timeout: 20000, what: 'the queued pit save to be delivered' });
      await until(
        () => page.evaluate(() => !Object.keys(localStorage).some((key) => key.includes('pit_draft'))),
        { timeout: 10000, what: 'the delivered draft to be cleared' },
      );
      const entry = server.pitById.get('2026arc|frc971');
      entry.payload = { ...entry.payload, notes: 'Bob: tank drive, climbs L2' };
      entry.updated_at = new Date().toISOString();
      await page.goto(`${BASE}/#/scouting/pit?event=2026arc`);
      const shown = await until(async () => {
        const value = await page.locator('textarea').first().inputValue().catch(() => '');
        return value.includes('Bob:') ? value : '';
      }, { timeout: 20000, what: "the teammate's newer notes" });
      check(shown.includes('Bob:'), 'old draft shown instead of newer notes');
    },
  },
  {
    name: 'an offline reload after a first visit still opens the page',
    async run(browser) {
      const ctx = await phone(browser, { serviceWorkers: 'allow' });
      const page = await ctx.newPage();
      await page.goto(BASE + BOARD);
      // Everything this page loaded must be cached (that's the promise), not just its own chunk.
      await until(() => page.evaluate(async () => {
        if (!navigator.serviceWorker.controller) return false;
        const loaded = [...new Set(performance.getEntriesByType('resource').map((entry) => entry.name))]
          .filter((url) => url.includes('/assets/') && !url.endsWith('.wasm'));
        for (const url of loaded) if (!(await caches.match(url))) return false;
        return loaded.length > 0;
      }), { timeout: 60000, what: "the worker to cache every chunk this page loaded" });
      const failed = [];
      page.on('requestfailed', (request) => { if (request.url().includes('/assets/')) failed.push(request.url()); });
      await ctx.setOffline(true);
      await page.reload().catch(() => {});
      await until(async () => /You are offline/.test(await bodyText(page)) && (await page.getByRole('button', { name: /^Save/ }).count()) > 0,
        { timeout: 15000, what: 'the scouting board offline' });
      check(failed.length === 0, `chunks failed offline: ${failed.slice(0, 3).join(', ')}`);
    },
  },
  {
    name: 'blocked or full storage keeps every page up',
    async run(browser) {
      for (const mode of ['blocked', 'full']) {
        const ctx = await phone(browser);
        await ctx.addInitScript((m) => {
          if (m === 'blocked') Object.defineProperty(window, 'localStorage', { get() { throw new DOMException('The operation is insecure.', 'SecurityError'); } });
          else Storage.prototype.setItem = function () { throw new DOMException('Quota exceeded', 'QuotaExceededError'); };
        }, mode);
        const page = await ctx.newPage();
        for (const route of ['/#/home', '/#/scouting?event=2026arc', '/#/team-center?team=frc254']) {
          await page.goto(BASE + route);
          await until(async () => (await bodyText(page)).length > 120, { timeout: 20000, what: `${route} to render with storage ${mode}` });
        }
        await ctx.close();
      }
    },
  },
];
