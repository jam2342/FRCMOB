// Bad wifi and a struggling server: no white screens, no stuck pages, no "0 matches"
// presented as fact. Each check is a bug the QA round of 2026-10-16 found (PR #183).
import { BASE, bodyText, check, desktop, phone, sleep, storedJson, until } from './lib.mjs';

const HTML_502 = '<!DOCTYPE html><html><body><h1>502 Bad Gateway</h1></body></html>';
// Home always shows its selected event's card once it has events.
const HOME_LOADED = /View Event/;

export default [
  {
    name: 'one extra failing to download leaves the page up',
    async run(browser) {
      const ctx = await phone(browser);
      const page = await ctx.newPage();
      const errors = [];
      page.on('pageerror', (error) => errors.push(String(error.message)));
      await page.route('**/assets/**', (route) => (/NextMatchPill/.test(route.request().url()) ? route.abort('connectionreset') : route.continue()));
      await page.goto(`${BASE}/#/home`);
      await until(async () => HOME_LOADED.test(await bodyText(page)), { timeout: 30000, what: 'Home to render without the next-match pill' });
      const rendered = await page.evaluate(() => document.getElementById('root')?.innerHTML.length ?? 0);
      check(rendered > 5000, `the app went blank (${rendered} chars)`);
    },
  },
  {
    name: "the app's own saved copies never block saving a scouting report",
    async run(browser) {
      const ctx = await phone(browser, { storage: { scouting_manual_profile_v1: 'Alice' } });
      const page = await ctx.newPage();
      page.on('dialog', (dialog) => dialog.accept().catch(() => {}));
      await page.goto(`${BASE}/#/scouting?event=2026arc&match=2026arc_qm7&team=frc1261`);
      const plus = page.getByRole('button', { name: /Increase TELEOP FUEL SCORED/i }).first();
      await plus.waitFor({ timeout: 30000 });
      // Fill storage to the browser's limit with saved API copies, as browsing does.
      const filled = await page.evaluate(() => {
        const chunk = 'x'.repeat(200_000);
        let count = 0;
        try {
          for (; count < 200; count += 1) {
            localStorage.setItem(`scouting_api_cache_v1:fill-${count}`, `{"storedAtMs":${count + 1},"bodyText":"${chunk}"}`);
          }
        } catch {
          // Full: that's the point.
        }
        return count;
      });
      check(filled > 5, `could not fill storage (${filled})`);
      await plus.tap();
      await page.getByRole('button', { name: /^Save/ }).first().tap();
      await sleep(1500);
      const text = await bodyText(page);
      check(!/storage is full/i.test(text), 'Save reported full storage');
      const saved = await storedJson(page, 'scouting_manual_entries_v2');
      check(saved.some((entry) => entry.match_key === '2026arc_qm7'), 'the report is not on the phone');
    },
  },
  {
    name: 'a server error shows the saved copy, not an empty day',
    async run(browser) {
      const ctx = await phone(browser);
      const first = await ctx.newPage();
      await first.goto(`${BASE}/#/home`);
      await until(async () => HOME_LOADED.test(await bodyText(first)), { timeout: 30000, what: 'Home on the first visit' });
      await sleep(3000);
      // Come back two hours later: every saved copy is past its fresh time.
      await first.evaluate(async () => {
        for (let i = 0; i < localStorage.length; i += 1) {
          const key = localStorage.key(i);
          if (!key?.startsWith('scouting_api_cache_v1:')) continue;
          const value = JSON.parse(localStorage.getItem(key));
          for (const field of ['storedAtMs', 'expiresAtMs']) if (value[field]) value[field] -= 2 * 3600e3;
          localStorage.setItem(key, JSON.stringify(value));
        }
      });
      await first.close();
      const page = await ctx.newPage();
      await page.route('**/api/**', (route) => route.fulfill({ status: 502, contentType: 'text/html', body: HTML_502 }));
      await page.goto(`${BASE}/#/home`);
      await until(async () => {
        const text = await bodyText(page);
        return HOME_LOADED.test(text) && /Showing data from last session/.test(text);
      }, { timeout: 25000, what: 'saved Home data with the "last session" note' });
      check(!/You are offline/.test(await bodyText(page)), 'a server error was reported as being offline');
    },
  },
  {
    name: 'Home recovers by itself after a short outage',
    async run(browser) {
      const ctx = await phone(browser);
      const page = await ctx.newPage();
      let down = true;
      await page.route('**/api/**', (route) => (down ? route.fulfill({ status: 502, contentType: 'text/html', body: HTML_502 }) : route.continue()));
      await page.goto(`${BASE}/#/home`);
      await sleep(15000);
      down = false;
      await until(async () => HOME_LOADED.test(await bodyText(page)), { timeout: 60000, every: 2000, what: 'Home to load after the server came back' });
    },
  },
  {
    name: 'a stalled first request does not leave Home loading forever',
    async run(browser) {
      const ctx = await phone(browser);
      const page = await ctx.newPage();
      await page.route('**/api/events/suggested**', () => undefined); // never answers
      await page.goto(`${BASE}/#/home`);
      await until(async () => HOME_LOADED.test(await bodyText(page)), { timeout: 40000, every: 2000, what: 'Home to load without /events/suggested' });
    },
  },
  {
    name: "Team Center says when a team's numbers didn't load",
    async run(browser) {
      for (const size of ['phone', 'desktop']) {
        const ctx = size === 'phone' ? await phone(browser) : await desktop(browser);
        const page = await ctx.newPage();
        await page.route('**/api/teams/frc254/intel**', () => undefined); // stalls
        await page.goto(`${BASE}/#/team-center?team=frc254&event=2026arc`);
        await until(async () => /numbers didn't load/.test(await bodyText(page)), { timeout: 60000, every: 2000, what: `the failure notice on ${size}` });
        await ctx.close();
      }
    },
  },
];
