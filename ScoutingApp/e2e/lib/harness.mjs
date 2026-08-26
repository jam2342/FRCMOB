// Shared browser harness for the guard scripts.

export const BASE_URL = process.env.GUARD_BASE_URL || 'http://127.0.0.1:5199';

// Every route in RootApp that renders a real page. Redirect-only paths
// (/export, /teams, /alliance-advisor, …) are deliberately absent — they render
// the target page, so including them would measure the same screen twice.
//
// These are hash fragments, not paths: RootApp mounts a HashRouter, so the real
// URL is `/#/settings`. Requesting `/settings` gets the dev server's SPA
// fallback with an empty hash, and RootApp's `path="*"` catch-all then renders
// Home — silently, with the correct pathname in the address bar.
export const ROUTES = [
  '/home',
  '/events',
  '/events/export',
  '/events/dashboard',
  '/scouting',
  '/scouting/pit',
  '/scouting/assignments',
  '/scouting/coverage',
  '/scouting/auto-paths',
  '/scouting/calibrate',
  '/scouting/record',
  '/team-center',
  '/match-center',
  '/match-center/predictions',
  '/match-center/strategy',
  '/compare',
  '/compare/alliance-advisor',
  '/compare/picklist',
  '/favorites',
  '/settings',
  '/privacy',
  '/terms',
  // The primitives gallery, twice: once at rest and once with a modal open.
  // A guard cannot click its way into an overlay, so the page reads the state
  // from the query string instead.
  '/primitives',
  '/primitives?state=modal',
];

// Selecting an event and a team turns most pages from an empty state into a
// populated one, which is where the data-shaped bugs live: a long team name
// overflowing a chip, a rating colour that only appears once there is a rating.
// Both are optional — with no backend running the guards still sweep every
// route, just against empty states.
export const SEED_EVENT_KEY = process.env.GUARD_EVENT_KEY || '';
export const SEED_TEAM_KEY = process.env.GUARD_TEAM_KEY || '';

const TUTORIAL_SCOPES = [
  'home', 'events', 'scouting', 'match-center', 'team-center',
  'compare', 'favorites', 'settings', 'ops',
];

// Suppress the onboarding overlay and pin the theme, so a guard measures the
// page rather than the tutorial modal sitting on top of it.
export async function newThemedContext(browser, { theme, width, height }) {
  const context = await browser.newContext({
    viewport: { width, height },
    deviceScaleFactor: 1,
    // A guard that samples mid-transition measures a colour that exists for
    // one frame and nowhere in the design. It also measures it in whatever
    // space the browser interpolates in — a fading chip reported itself as
    // `oklab(0.694 0.156 0.073 / 0.1718)`, which guard 3 could not convert and
    // so silently skipped. Freezing motion makes every sample a real one.
    reducedMotion: 'reduce',
  });

  await context.addInitScript(() => {
    const freeze = () => {
      const style = document.createElement('style');
      style.textContent = `*, *::before, *::after {
        transition: none !important;
        animation: none !important;
        scroll-behavior: auto !important;
      }`;
      document.head.appendChild(style);
    };
    if (document.head) freeze();
    else document.addEventListener('DOMContentLoaded', freeze, { once: true });
  });
  await context.addInitScript(
    ([themeMode, scopes, eventKey, teamKey]) => {
      localStorage.setItem('scouting_theme_mode', themeMode);
      localStorage.setItem('scouting_tutorial_autoplay', 'false');
      const seen = {};
      for (const scope of scopes) seen[scope] = 99;
      localStorage.setItem('scouting_tutorial_seen_v1', JSON.stringify(seen));
      if (eventKey) {
        localStorage.setItem('scouting_center_event_key', eventKey);
        localStorage.setItem('scouting_compare_event_key', eventKey);
      }
      if (teamKey) {
        localStorage.setItem('scouting_center_team_key', teamKey);
        localStorage.setItem('scouting_compare_team_keys', JSON.stringify([teamKey]));
      }
    },
    [theme, TUTORIAL_SCOPES, SEED_EVENT_KEY, SEED_TEAM_KEY],
  );
  return context;
}

// The backend is usually down when guards run, so `networkidle` never settles.
//
// Every route gets its own document. A hash-only `goto` would not reload, and
// Playwright resolves it before React has swapped the page — so the guard would
// measure the *previous* route's DOM. Loading `/#/route` fresh each time costs a
// little time and removes that whole class of silent wrong answer.
export async function gotoRoute(page, route) {
  try {
    await page.goto(`${BASE_URL}/#${route}`, { waitUntil: 'domcontentloaded', timeout: 15000 });
  } catch {
    return false;
  }

  // Pages are lazy chunks behind Suspense. Wait for the spinner to clear rather
  // than guessing at a sleep, or a slow chunk is measured as an empty page.
  try {
    await page.waitForFunction(() => !document.querySelector('.page-spinner'), null, { timeout: 15000 });
  } catch {
    return false;
  }

  // Some pages redirect from an effect once they discover there is no selected
  // event, so the hash can still change after the first paint. Sleeping a fixed
  // amount races that: the same route was measured as itself in one guard and as
  // a redirect in another. Wait for the hash to hold still instead.
  // With a backend attached, pages keep fetching after first paint. Capturing
  // mid-flight made snapshots differ run to run for no real reason, so wait for
  // the request queue to go quiet. Bounded, and a no-op when nothing is in
  // flight — so this stays fast with no backend running.
  await settleNetwork(page);

  const landed = await settleHash(page);

  // Guard against the failure this function exists to prevent: if the router
  // bounced us somewhere else, say so instead of silently measuring Home.
  // Compare paths only — a route may carry a query string that puts the page
  // into a particular state, and `settleHash` already strips it.
  const expected = route.split('?')[0];
  if (landed !== expected) {
    console.warn(`  ! ${route} redirected to ${landed || '/'} — skipped`);
    return false;
  }

  return true;
}

// Resolve once no app request has started or finished for `quietMs`. This is
// `networkidle` in spirit, but tolerant of the polling this app does on purpose
// (live ratings poll on an interval and would keep networkidle from ever firing).
//
// The timeout is 25s rather than 8s because the team-intel endpoint takes about
// 30s on a cold cache and under 100ms warm. At 8s the guards gave up mid-flight,
// the page rendered its empty state, and settleContent below then reported a
// perfectly stable empty page — /match-center measured 52 text nodes on one run
// and 348 on the next. "Finished rendering" and "never arrived" look identical
// from the outside, so the wait has to outlast the slowest real response.
async function settleNetwork(page, { quietMs = 700, timeoutMs = 25000 } = {}) {
  let inflight = 0;
  let lastActivity = Date.now();
  const onRequest = () => { inflight += 1; lastActivity = Date.now(); };
  const onDone = () => { inflight = Math.max(0, inflight - 1); lastActivity = Date.now(); };
  page.on('request', onRequest);
  page.on('requestfinished', onDone);
  page.on('requestfailed', onDone);

  const deadline = Date.now() + timeoutMs;
  try {
    while (Date.now() < deadline) {
      await page.waitForTimeout(120);
      if (inflight === 0 && Date.now() - lastActivity >= quietMs) return;
    }
  } finally {
    page.off('request', onRequest);
    page.off('requestfinished', onDone);
    page.off('requestfailed', onDone);
  }
}

// Poll until the hash has been unchanged across two consecutive reads, or we
// run out of patience. Returns the route the page actually came to rest on.
async function settleHash(page, { quietMs = 500, timeoutMs = 6000 } = {}) {
  const readHash = () => page.evaluate(() => location.hash.replace(/^#/, '').split('?')[0]);
  const deadline = Date.now() + timeoutMs;
  let previous = await readHash();

  while (Date.now() < deadline) {
    await page.waitForTimeout(quietMs);
    const current = await readHash();
    if (current === previous) return current;
    previous = current;
  }
  return previous;
}

export async function assertServerUp() {
  try {
    const response = await fetch(BASE_URL, { signal: AbortSignal.timeout(4000) });
    if (response.ok) return;
  } catch {
    // fall through
  }
  console.error(
    `\nCannot reach ${BASE_URL}.\n` +
    `Start the app first:  npm run dev -- --port 5199 --host 127.0.0.1\n` +
    `Or point the guard elsewhere with GUARD_BASE_URL.\n`,
  );
  process.exit(2);
}

/* Wait until the page stops growing text.
 *
 * `gotoRoute` settles the network, which is not the same thing: React renders
 * after the response lands, and a route that polls can reset the quiet window
 * before the first paint of its data. A guard that measures too early sees a
 * different page each run — /match-center reported 57 text nodes on one pass
 * and 356 on the next, and a median computed over 57 nodes is not the same
 * measurement.
 *
 * Counting rendered text is a better signal than a fixed sleep because it
 * finishes as soon as the page is done rather than always costing the
 * worst case, and it says so when a page never settles.
 */
export async function settleContent(page, { stableFor = 2, intervalMs = 400, timeoutMs = 9000, minChars = 400, attempt = 0 } = {}) {
  const MAX_ATTEMPTS = 3;
  const started = Date.now();
  let last = -1;
  let stable = 0;
  while (Date.now() - started < timeoutMs) {
    // The CONTENT region, not the whole body. The shell chrome — nav, topbar,
    // context strip — is about 200 characters on every route, so a page that
    // failed to load still clears any floor set against document.body. That is
    // why the first version of this retry never fired: /match-center rendered
    // nothing and still measured 600+.
    const n = await page.evaluate(() =>
      (document.querySelector('.ps-content') || document.body).innerText.length);
    if (n === last) {
      stable += 1;
      if (stable >= stableFor) {
        // Settling small usually means a request the guard stopped waiting for,
        // not a page that is finished. The sparsest real route here (Favorites,
        // empty) renders about 510 characters, so 400 sits below every genuine
        // page and above pure chrome. Reload and try again rather than
        // recording an empty page as the truth.
        if (n < minChars && attempt + 1 < MAX_ATTEMPTS) {
          await page.reload({ waitUntil: 'domcontentloaded' });
          await page.waitForTimeout(500 * (attempt + 1));
          return settleContent(page, { stableFor, intervalMs, timeoutMs, minChars, attempt: attempt + 1 });
        }
        return true;
      }
    } else {
      stable = 0;
      last = n;
    }
    await page.waitForTimeout(intervalMs);
  }
  return false;
}
