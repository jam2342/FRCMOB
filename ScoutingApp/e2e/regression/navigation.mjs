// Moving around on a phone: the right page, nothing left covering it.
// Each check is a bug the QA round of 2026-10-16 found (PR #182).
import { BASE, check, phone, sleep, until } from './lib.mjs';

const hash = (page) => page.evaluate(() => location.hash);
const openSearch = (page) => page.locator('.ps-topbar button[aria-label*=earch i]').first().tap();

export default [
  {
    name: 'searching an event name from the header opens that event',
    async run(browser) {
      const ctx = await phone(browser);
      const page = await ctx.newPage();
      // Having viewed another event before is what made it open the wrong one.
      await page.goto(`${BASE}/#/events?event=2026cur`);
      await sleep(3000);
      await page.goto(`${BASE}/#/home`);
      await sleep(2500);
      await openSearch(page);
      const box = page.getByPlaceholder(/Search teams/i);
      await box.waitFor({ timeout: 5000 });
      await box.fill('archimedes');
      await box.press('Enter');
      await until(async () => (await hash(page)).includes('event=2026arc'), { timeout: 20000, what: 'Archimedes (2026arc) to open' });
    },
  },
  {
    name: 'Back closes the More sheet and the search overlay',
    async run(browser) {
      for (const which of ['more', 'search']) {
        const ctx = await phone(browser);
        const page = await ctx.newPage();
        await page.goto(`${BASE}/#/home`);
        await sleep(2000);
        await page.goto(`${BASE}/#/team-center?team=frc1678`);
        await sleep(2500);
        if (which === 'more') await page.locator('.btb__tab--more').tap();
        else await openSearch(page);
        await sleep(600);
        await page.goBack();
        await until(() => page.evaluate(() => !document.querySelector('#mobile-more-sheet') && !document.querySelector('.mso') && document.body.style.overflow === ''),
          { timeout: 5000, what: `the ${which} overlay to close and scrolling to unlock` });
        await ctx.close();
      }
    },
  },
  {
    name: 'the selected tab is on screen when the strip is wider than the phone',
    async run(browser) {
      const ctx = await phone(browser);
      const page = await ctx.newPage();
      await page.goto(`${BASE}/#/events?event=2026arc&tab=teams`);
      await until(() => page.evaluate(() => {
        const list = document.querySelector('.ps-content [role=tablist]');
        const active = list?.querySelector('[aria-selected=true]');
        if (!active) return false;
        const box = list.getBoundingClientRect();
        const tab = active.getBoundingClientRect();
        return tab.left >= box.left - 1 && tab.right <= box.right + 1;
      }), { timeout: 15000, what: 'the Teams tab to be visible in the strip' });
    },
  },
  {
    name: 'scrolling the More sheet back up on a small phone keeps it open',
    async run(browser) {
      const ctx = await phone(browser, { width: 250, height: 450 });
      const page = await ctx.newPage();
      await page.goto(`${BASE}/#/home`);
      await sleep(2500);
      await page.locator('.btb__tab--more').tap();
      await sleep(600);
      const cdp = await ctx.newCDPSession(page);
      // A finger moving down the sheet: real touch events, as the sheet listens for them.
      const dragDown = async () => {
        await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: 125, y: 150 }] });
        for (let step = 1; step <= 6; step += 1) {
          await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: 125, y: 150 + step * 20 }] });
        }
        await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
        await sleep(600);
      };
      const scrolled = await page.evaluate(() => {
        const sheet = document.querySelector('#mobile-more-sheet');
        if (!sheet) return -1;
        sheet.scrollTop = 200;
        return sheet.scrollTop;
      });
      check(scrolled > 0, `the sheet does not scroll at this size (${scrolled})`);
      await dragDown();
      check(await page.locator('#mobile-more-sheet').count() === 1, 'a drag down inside the scrolled sheet closed it');
      // And from the top the same drag does close it, so the gesture really lands.
      await page.evaluate(() => { document.querySelector('#mobile-more-sheet').scrollTop = 0; });
      await dragDown();
      check(await page.locator('#mobile-more-sheet').count() === 0, 'a drag down from the top did not close the sheet');
    },
  },
  {
    name: 'a Data Dashboard link opened on a phone keeps its event',
    async run(browser) {
      const ctx = await phone(browser);
      const page = await ctx.newPage();
      await page.goto(`${BASE}/#/events/dashboard?event=2026arc`);
      await until(async () => /^#\/events\?.*event=2026arc/.test(await hash(page)), { timeout: 15000, what: 'Events with ?event=2026arc' });
    },
  },
];
