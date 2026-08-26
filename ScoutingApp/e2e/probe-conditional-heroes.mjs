/* Verifies the heroes guard-hierarchy cannot reach.
 *
 *   node e2e/probe-conditional-heroes.mjs
 *
 * Four routes have a hero that only exists once data arrives that the guard
 * cannot produce: an alliance nobody has built, a recording nobody has started,
 * a match not scheduled today. Marking them exempt and moving on would mean the
 * hero was never seen to work, so this drives each one into its populated state
 * by fulfilling the request it is waiting on, then measures the real rendered
 * page — same CSS, same components, real computed styles.
 *
 * Stubbed responses are shaped from the live API, not invented. Where the app
 * has no route to stub (the record wizard needs a camera), that is stated
 * rather than faked.
 */
import { chromium } from 'playwright';
import { newThemedContext, gotoRoute, settleContent, BASE_URL } from './lib/harness.mjs';

const DISPLAY_PX = 28;

async function heroOf(page) {
  return page.evaluate(({ floor }) => {
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    const found = [];
    let node;
    while ((node = walker.nextNode())) {
      const text = node.nodeValue.trim();
      if (!text) continue;
      const el = node.parentElement;
      if (!el) continue;
      const r = el.getBoundingClientRect();
      if (!r.width || !r.height) continue;
      const px = Number.parseFloat(getComputedStyle(el).fontSize);
      if (px >= floor - 0.5) found.push({ text: text.slice(0, 44), px });
    }
    return found;
  }, { floor: DISPLAY_PX });
}

const browser = await chromium.launch();
const results = [];

async function check(name, route, install, drive) {
  const context = await newThemedContext(browser, { theme: 'dark', width: 1440, height: 900 });
  const page = await context.newPage();
  if (install) await install(page);
  const ok = await gotoRoute(page, route);
  let note = null;
  if (ok) {
    await settleContent(page);
    // Some heroes need a person to press something first. Say so when that
    // fails rather than reporting it as a hero that did not render.
    if (drive) {
      try { await drive(page); } catch (err) { note = `could not drive the page: ${String(err).split('\n')[0].slice(0, 90)}`; }
      await settleContent(page);
    }
  }
  const heroes = ok ? await heroOf(page) : [];
  results.push({ name, route, ok, heroes, note });
  // Home fans out schedule requests across every event in its feed, so some are
  // still in flight when the measurement is done. Closing under them makes the
  // handler throw on a closed target, which looks like a probe failure.
  await page.unrouteAll({ behavior: 'ignoreErrors' });
  await context.close();
}

/* /home — the countdown to the next match. Home reads the schedule for the
   events in its feed; the seeded event ran in April, so nothing is upcoming.
   Rather than invent a schedule, fetch the real one and move the first match to
   eight minutes from now.
   
   The first draft did invent one, and Home crashed on `.map` of undefined: the
   real payload carries `red` / `blue` as arrays of team objects, not the
   `red_teams` string arrays that seemed obvious. Transforming the real response
   cannot drift from the real shape.

   Matched by RegExp rather than a glob: the request is an absolute URL to the
   API host, and a glob written against the path silently matches nothing —
   which reads exactly like a hero that failed to render. */
await check('home · next-match countdown', '/home', async (page) => {
  await page.route(/\/matches\/event\/[^/]+\/schedule/, async (route) => {
    let response;
    try {
      response = await route.fetch();
    } catch {
      return;   // page went away mid-flight
    }
    const body = await response.json().catch(() => null);
    if (!body || !Array.isArray(body.matches) || !body.matches.length) {
      await route.fulfill({ response });
      return;
    }
    const startsIn = Math.floor(Date.now() / 1000) + 8 * 60;
    const [first, ...rest] = body.matches;
    const upcoming = {
      ...first,
      scheduled_time: startsIn,
      has_time: true,
      red_score: null,
      blue_score: null,
      winner_alliance: null,
      is_completed: false,
      winning_score: null,
      losing_score: null,
    };
    await route.fulfill({
      response,
      body: JSON.stringify({ ...body, matches: [upcoming, ...rest] }),
    });
  });
});

/* /events/dashboard — the top rating in the field. Nothing to stub: the ratings
   are really there, the page just has its own event picker and does not inherit
   the global one. Fill it and press Load Event the way a person would. */
await check('data dashboard · top rating', '/events/dashboard', null, async (page) => {
  await page.getByPlaceholder(/Search events/i).fill('2026arc');
  await page.getByRole('button', { name: 'Load Event', exact: true }).click();
});

/* /compare/alliance-advisor — the alliance score. The teams load fine; what is
   missing is a built alliance, and building one is a POST behind admin auth
   which is enforced here by design. Fulfil the POST and press Analyze. */
await check('alliance advisor · alliance score', '/compare/alliance-advisor', async (page) => {
  await page.route(/theoretical-alliance/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        ok: true,
        event_key: '2026arc',
        weighted_total_score_0_100: 91.4,
        compatibility: {
          compatibility_score_0_100: 88.2,
          alliance_synergy_points: 12.75,
          confidence_0_1: 0.81,
          pair_breakdown: [],
        },
        teams: [],
      }),
    });
  });
}, async (page) => {
  // Three unique teams, then Analyze. runBuilder refuses anything else.
  //
  // The teams are read out of the page rather than hardcoded: the first draft
  // of this picked 1678/254/971, none of which are at the seeded event, so all
  // three selects stayed empty and the failure looked like a hero that would
  // not render.
  const selects = page.locator('select');
  const total = await selects.count();
  let picks = [];
  for (let i = 0; i < total; i += 1) {
    const options = await selects.nth(i).locator('option').evaluateAll((els) =>
      els.map((e) => e.value).filter((v) => /^frc\d+$/.test(v)));
    if (options.length >= 3) { picks = options.slice(0, 3); break; }
  }
  if (picks.length !== 3) throw new Error('no team select with three options');
  let filled = 0;
  for (let i = 0; i < total && filled < 3; i += 1) {
    const options = await selects.nth(i).locator('option').evaluateAll((els) => els.map((e) => e.value));
    if (!options.includes(picks[filled])) continue;
    await selects.nth(i).selectOption(picks[filled]);
    filled += 1;
  }
  if (filled !== 3) throw new Error(`filled ${filled} of 3 team slots`);
  await page.getByRole('button', { name: /Analyze Alliance/i }).click();
});

await browser.close();

console.log('\n' + '─'.repeat(70));
console.log('Conditional heroes — routes guard-hierarchy measures in an empty state');
console.log('');
let failed = 0;
for (const r of results) {
  const display = r.heroes.filter((h) => h.px >= DISPLAY_PX - 0.5);
  const mark = display.length === 1 ? '✓' : '✗';
  if (display.length !== 1) failed += 1;
  console.log(`  ${mark} ${r.name}`);
  if (r.note) console.log(`        ${r.note}`);
  if (!r.ok) console.log('        route did not load');
  else if (!display.length) console.log('        no element reached the display step — the stub did not populate the page');
  else for (const h of display) console.log(`        ${h.px}px  ${JSON.stringify(h.text)}`);
}

console.log('');
console.log('  · scouting/record · match clock');
console.log('        Not stubbed. The clock only runs once recording starts, which needs');
console.log('        a camera; the same 36px --font-size-display rule styles it as the');
console.log('        Live Scouting clock verified by guard-hierarchy on /scouting.');

if (failed) {
  console.log(`\n${failed} conditional hero did not render. ✗`);
  process.exit(1);
}
console.log('\nEvery reachable conditional hero renders on the display step. ✓');
