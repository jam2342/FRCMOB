/* Guard 8 — box census.
 *
 *   node e2e/guard-boxes.mjs            # check
 *   node e2e/guard-boxes.mjs --update   # re-record the ceilings
 *
 * A per-route ceiling on the number of bordered, rounded, filled containers.
 * Events rendered 170 of them at baseline, on a page whose job is to list about
 * twenty events; Match Center rendered 91. One surface colour appeared 175
 * times on a single screen.
 *
 * The rule this enforces, from src/styles/README.md:
 *
 *     A box means "this is a thing", not "these are related".
 *     Related is what space is for.
 *
 * A budget rather than a target, for the same reason as the colour and type
 * guards: a hard number would be arbitrary per route, whereas "fewer than
 * yesterday" is always right. Adding a box fails; removing one is reported so
 * the ceiling can be lowered.
 *
 * Nested boxes are counted separately and reported even when the total is
 * under budget. A box inside a box is the clearest form of the violation — an
 * empty state inside a card — and it can hide under a falling total.
 */
import { chromium } from 'playwright';
import { readFileSync, writeFileSync, existsSync } from 'node:fs';
import { join, dirname, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { ROUTES, newThemedContext, gotoRoute, settleContent, BASE_URL } from './lib/harness.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, '..');
const BASELINE = join(HERE, 'baselines', 'box-budget.json');
const UPDATE = process.argv.includes('--update');

async function census(page) {
  return page.evaluate(() => {
    // What counts as a box: visible border, a radius you would notice, a fill
    // distinct from transparent, and big enough to read as a container rather
    // than as a chip or a badge. A chip is a box shape but it is not a
    // container, and banning chips is not what this is for.
    const MIN_W = 140;
    const MIN_H = 56;
    const boxes = [...document.querySelectorAll('body *')].filter((el) => {
      const s = getComputedStyle(el);
      if (s.display === 'none' || s.visibility === 'hidden') return false;
      if (parseFloat(s.borderTopWidth) < 1) return false;
      if (parseFloat(s.borderTopLeftRadius) < 6) return false;
      if (s.backgroundColor === 'rgba(0, 0, 0, 0)' || s.backgroundColor === 'transparent') return false;
      const r = el.getBoundingClientRect();
      return r.width >= MIN_W && r.height >= MIN_H;
    });
    const set = new Set(boxes);
    let nested = 0;
    const nestedSamples = [];
    for (const el of boxes) {
      for (let p = el.parentElement; p; p = p.parentElement) {
        if (set.has(p)) {
          nested += 1;
          if (nestedSamples.length < 4) {
            nestedSamples.push(`${String(p.className || p.tagName).slice(0, 28)} > ${String(el.className || el.tagName).slice(0, 28)}`);
          }
          break;
        }
      }
    }
    return { boxes: boxes.length, nested, nestedSamples };
  });
}

const browser = await chromium.launch();
const context = await newThemedContext(browser, { theme: 'dark', width: 1440, height: 900 });
const page = await context.newPage();

try {
  await page.goto(BASE_URL, { waitUntil: 'domcontentloaded', timeout: 10000 });
} catch {
  console.error(`Guard 8 needs the app running at ${BASE_URL}. Set GUARD_BASE_URL.`);
  process.exit(2);
}

const current = {};
const detail = {};
for (const route of ROUTES) {
  if (!await gotoRoute(page, route)) continue;
  // Measuring a half-rendered page gives a different answer every run.
  if (!await settleContent(page)) console.warn(`  ! ${route} never stopped rendering — measured anyway`);
  const c = await census(page);
  current[route] = { boxes: c.boxes, nested: c.nested };
  detail[route] = c.nestedSamples;
}
await browser.close();

if (UPDATE || !existsSync(BASELINE)) {
  writeFileSync(BASELINE, `${JSON.stringify(current, null, 2)}\n`);
  const total = Object.values(current).reduce((a, v) => a + v.boxes, 0);
  console.log(`Baseline written: ${Object.keys(current).length} routes, ${total} boxes -> ${relative(ROOT, BASELINE)}`);
  process.exit(0);
}

const baseline = JSON.parse(readFileSync(BASELINE, 'utf8'));
const total = Object.values(current).reduce((a, v) => a + v.boxes, 0);
const baseTotal = Object.values(baseline).reduce((a, v) => a + v.boxes, 0);

console.log('\n' + '─'.repeat(64));
console.log(`Guard 8 — box census: ${total} across ${Object.keys(current).length} routes (baseline ${baseTotal})`);
console.log('');

const over = [];
const under = [];
const nested = [];
for (const route of Object.keys(current)) {
  const was = baseline[route]?.boxes ?? 0;
  const now = current[route].boxes;
  if (!(route in baseline)) continue;   // new route: no ceiling to exceed yet
  if (now > was) over.push({ route, was, now });
  else if (now < was) under.push({ route, was, now });
  if (current[route].nested > 0) nested.push({ route, n: current[route].nested, samples: detail[route] });
}

for (const route of Object.keys(current).sort((a, b) => current[b].boxes - current[a].boxes).slice(0, 8)) {
  const was = baseline[route]?.boxes ?? 0;
  const arrow = current[route].boxes === was ? ' ' : current[route].boxes < was ? '↓' : '↑';
  console.log(`  ${route.padEnd(28)} ${String(current[route].boxes).padStart(4)} ${arrow}  (was ${was})`);
}

if (nested.length) {
  console.log(`\nnested boxes — a box inside a box says "related", which is what space is for:`);
  for (const n of nested.slice(0, 8)) {
    console.log(`  ${n.route.padEnd(28)} ${n.n}`);
    for (const s of n.samples) console.log(`        ${s}`);
  }
}

if (under.length) {
  console.log(`\n${under.length} route(s) improved — re-run with --update to lock the gain in:`);
  for (const u of under) console.log(`  ${u.route.padEnd(28)} ${u.was} -> ${u.now}`);
}

if (!over.length) {
  console.log('\nNo route gained a box. ✓');
  process.exit(0);
}

console.log(`\n${over.length} ROUTE(S) OVER BUDGET:\n`);
for (const o of over) console.log(`  ${o.route.padEnd(28)} ${o.was} -> ${o.now}`);
console.log('\nA box is for something you can act on, or that stands for a real entity —');
console.log('a match, a team, an assignment. Not for "these four numbers are related".');
console.log('SurfaceCard takes level="plain" | "section" | "card"; see src/styles/README.md.');
process.exit(1);
