/* Guard 7 — hierarchy.
 *
 *   node e2e/guard-hierarchy.mjs            # check
 *   node e2e/guard-hierarchy.mjs --update   # re-record what each route reaches
 *
 * The five guards that came before this one check correctness: contrast,
 * overflow, affordance names, raw colour, raw size. Every failure in the design
 * teardown passed all five, because flat passes contrast. This is the one that
 * can fail on flat.
 *
 * Two assertions, both mechanical:
 *
 *   dynamic range  largest text on the route divided by its median. A page
 *                  with a working hierarchy runs 3-5x. Home measured 1.25x —
 *                  15px largest against a 12px median. Match Center already
 *                  ran 2.67x before any of this work, so the floor here is an
 *                  observed number rather than an aspiration.
 *
 *   one hero       exactly one element on the display step, and its text is a
 *                  value rather than a label. Routes that legitimately have no
 *                  hero — a list, a form, a canvas — are named in
 *                  src/styles/README.md and listed as HEROLESS below.
 *
 * Median is weighted by text node, not by distinct size: one <h1> and four
 * hundred table cells should give a median of the table cell.
 */
import { chromium } from 'playwright';
import { readFileSync, writeFileSync, existsSync } from 'node:fs';
import { join, dirname, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { ROUTES, newThemedContext, gotoRoute, settleContent, BASE_URL } from './lib/harness.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, '..');
const BASELINE = join(HERE, 'baselines', 'hierarchy.json');
const UPDATE = process.argv.includes('--update');

const MIN_RANGE = 2.5;
const DISPLAY_PX = 36;      // --font-size-display
const HERO_FLOOR_PX = 28;   // --font-size-3xl; the display step on mobile

/* Routes whose hero exists but needs data or input this guard cannot supply:
 * an alliance nobody has built, a recording nobody has started, a match that
 * is not scheduled today. They are reported as `~` and do not fail the run.
 *
 * They are NOT folded into HEROLESS, because that would say "this screen has
 * no hero" when what is true is "this screen has no data". The distinction
 * matters the first time someone reads this output on an event day and sees
 * these routes turn into ✓ or ✗ on their own.
 *
 * Each is verified separately by stubbing its response — see
 * e2e/probe-conditional-heroes.mjs. */
const CONDITIONAL = new Map([
  ['/home', 'no match scheduled today in the loaded feed'],
  ['/events/dashboard', 'no event loaded in this page\u2019s own picker'],
  ['/scouting/record', 'hero is the match clock; recording needs a camera'],
  ['/compare/alliance-advisor', 'needs an alliance built, which is a POST behind admin auth'],
]);

// Routes that are a list, a form, a canvas or a document. A screen is allowed
// to have no hero; it is not allowed to have four. Kept in step with the table
// in src/styles/README.md.
const HEROLESS = new Set([
  '/events', '/match-center/strategy', '/scouting/pit', '/scouting/assignments',
  '/scouting/auto-paths', '/scouting/calibrate', '/compare', '/compare/picklist',
  '/favorites', '/settings', '/privacy', '/terms', '/primitives',
  '/primitives?state=modal', '/events/export',
]);

async function measure(page) {
  return page.evaluate(({ displayPx }) => {
    // Walk real text nodes and take the computed size of the element that owns
    // them. Counting only childless elements undercounts a heading with a
    // nested span, which is most of the headings in this app.
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    const sizes = [];
    const heroes = [];
    let node;
    while ((node = walker.nextNode())) {
      const text = node.nodeValue.trim();
      if (!text) continue;
      const el = node.parentElement;
      if (!el || ['SCRIPT', 'STYLE', 'NOSCRIPT'].includes(el.tagName)) continue;
      const rect = el.getBoundingClientRect();
      if (rect.width === 0 || rect.height === 0) continue;
      const style = getComputedStyle(el);
      if (style.visibility === 'hidden' || style.display === 'none') continue;
      const px = Number.parseFloat(style.fontSize);
      sizes.push(px);
      if (px >= displayPx - 0.5) {
        heroes.push({
          text: text.slice(0, 40),
          px: Math.round(px * 10) / 10,
          cls: String(el.className || '').slice(0, 40),
          // A hero is a value. A 36px word that is a label is the failure this
          // catches: it means the page shouted the name of the thing instead of
          // the thing.
          looksNumeric: /[0-9]/.test(text),
        });
      }
    }
    if (!sizes.length) return null;
    sizes.sort((a, b) => a - b);
    const median = sizes[Math.floor(sizes.length / 2)];
    const max = sizes[sizes.length - 1];
    return {
      nodes: sizes.length,
      median: Math.round(median * 100) / 100,
      max: Math.round(max * 100) / 100,
      range: Math.round((max / median) * 100) / 100,
      heroes,
    };
  }, { displayPx: HERO_FLOOR_PX });
}

const browser = await chromium.launch();
const context = await newThemedContext(browser, { theme: 'dark', width: 1440, height: 900 });
const page = await context.newPage();

try {
  await page.goto(BASE_URL, { waitUntil: 'domcontentloaded', timeout: 10000 });
} catch {
  console.error(`Guard 7 needs the app running at ${BASE_URL}. Set GUARD_BASE_URL.`);
  process.exit(2);
}

const results = {};
for (const route of ROUTES) {
  if (!await gotoRoute(page, route)) continue;
  // Measuring a half-rendered page gives a different answer every run.
  if (!await settleContent(page)) console.warn(`  ! ${route} never stopped rendering — measured anyway`);
  const m = await measure(page);
  if (m) results[route] = m;
}
await browser.close();

if (UPDATE || !existsSync(BASELINE)) {
  const store = {};
  for (const [route, m] of Object.entries(results)) store[route] = { range: m.range, max: m.max, median: m.median, heroes: m.heroes.length };
  writeFileSync(BASELINE, `${JSON.stringify(store, null, 2)}\n`);
  console.log(`Baseline written for ${Object.keys(store).length} routes -> ${relative(ROOT, BASELINE)}`);
  process.exit(0);
}

console.log('\n' + '─'.repeat(72));
console.log(`Guard 7 — hierarchy: ${Object.keys(results).length} routes, floor ${MIN_RANGE}x dynamic range`);
console.log('');
console.log('  route                      nodes   median    max   range   hero');
console.log('  ' + '─'.repeat(68));

const failures = [];
const conditional = [];
for (const [route, m] of Object.entries(results)) {
  const heroless = HEROLESS.has(route);
  const heroCount = m.heroes.length;
  const displayHeroes = m.heroes.filter((h) => h.px >= DISPLAY_PX - 0.5);

  // A route that has its hero anyway is held to the normal rules — the
  // exemption is for the empty state, not for the route.
  const unpopulated = CONDITIONAL.has(route) && heroCount === 0;
  if (unpopulated) conditional.push({ route, why: CONDITIONAL.get(route) });

  const problems = [];
  if (m.range < MIN_RANGE && !heroless && !unpopulated) problems.push(`range ${m.range}x below ${MIN_RANGE}x`);
  if (!heroless && !unpopulated && heroCount === 0) problems.push('no hero — README names one for this route');
  if (heroless && displayHeroes.length > 0) problems.push(`${displayHeroes.length} display element(s) on a route README says has no hero`);
  if (heroCount > 1) {
    const distinct = new Set(m.heroes.map((h) => h.text));
    // The same value echoed at the same size in two places is one hero rendered
    // twice, which is its own bug but not this one.
    if (distinct.size > 1) problems.push(`${distinct.size} competing heroes: ${[...distinct].slice(0, 3).map((t) => JSON.stringify(t)).join(', ')}`);
  }
  const labelHero = m.heroes.find((h) => !h.looksNumeric);
  if (labelHero && !heroless) problems.push(`hero is a label, not a value: ${JSON.stringify(labelHero.text)}`);

  const mark = problems.length ? '✗' : unpopulated ? '~' : heroless ? '·' : '✓';
  console.log(
    `  ${mark} ${route.padEnd(24)} ${String(m.nodes).padStart(5)} ${String(m.median).padStart(8)} ${String(m.max).padStart(6)} ${(m.range + 'x').padStart(7)}   ${heroCount || (heroless ? '—' : '0')}`,
  );
  if (problems.length) failures.push({ route, problems });
}

if (conditional.length) {
  console.log(`\n~ ${conditional.length} route(s) measured in an empty state — hero not reachable from here:`);
  for (const c of conditional) console.log(`    ${c.route.padEnd(28)} ${c.why}`);
  console.log('  Verify these with: node e2e/probe-conditional-heroes.mjs');
}

if (!failures.length) {
  console.log('\nEvery route that could be measured has a hierarchy. ✓');
  process.exit(0);
}

console.log(`\n${failures.length} route(s) failing:\n`);
for (const f of failures) {
  console.log(`  ${f.route}`);
  for (const p of f.problems) console.log(`      ${p}`);
  console.log();
}
console.log('Give the route the hero src/styles/README.md names for it, on');
console.log('--font-size-display. If the route genuinely has no hero — a list, a form,');
console.log('a canvas — add it to HEROLESS here and to the table in the README.');
process.exit(1);
