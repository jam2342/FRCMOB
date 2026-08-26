/* Guard 9 — cross-route consistency.
 *
 *   node e2e/guard-consistency.mjs            # check
 *   node e2e/guard-consistency.mjs --update   # re-record the baseline
 *
 * The other eight guards each look at one page at a time: is this colour a
 * token, is this text readable, does this route have a hero. None of them can
 * see the class of bug that produced the last five fixes on this branch,
 * because every one of those was two pages disagreeing with each other:
 *
 *   - Live Scouting had no gap under the view bar; Pit Scouting had 16px,
 *     because the gap lived in each page's own wrapper rather than on the bar.
 *   - Live Scouting's workspace column inherited the sticky, height-capped,
 *     independently-scrolling treatment meant for a *finder*, so the page
 *     would not scroll.
 *   - Three panel toggles were on screen at once, all reading "Collapse".
 *
 * Three checks, all of them about agreement rather than correctness.
 *
 *   offset      Routes that share a view bar must start their content at the
 *               same place. Siblings in one nav group that do not line up is
 *               always a bug, never a decision.
 *   scrollers   Any scroll container inside .ps-content is recorded. A new one
 *               is usually accidental — `overflow-x: hidden` computes the other
 *               axis to `auto`, which is how one appeared here unasked.
 *   card-gap    The gap between sibling cards, which is one role and had five
 *               values: 16px on nineteen routes, and 10, 11, 12 and 14 across
 *               Live Scouting and Compare. `.center-main` alone was declared in
 *               three files with three different numbers.
 *   name        One destination, one name. The same page was "Compare" and
 *               "Open Builder"; "Events" and "Event Center". A button that
 *               looks like a different feature but goes to the same place is
 *               most of what "four buttons that do the same thing" means.
 *   duplicates  Visible controls sharing an accessible name on one route.
 *               "Collapse" three times is the reported bug; a baseline rather
 *               than a ban, because a table of identical row actions is fine.
 */
import { chromium } from 'playwright';
import { readFileSync, writeFileSync, existsSync } from 'node:fs';
import { join, dirname, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { ROUTES, newThemedContext, gotoRoute, settleContent, BASE_URL } from './lib/harness.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, '..');
const BASELINE = join(HERE, 'baselines', 'consistency.json');
const UPDATE = process.argv.includes('--update');

// Sub-pixel differences come from fractional layout, not from a mistake.
const OFFSET_TOLERANCE_PX = 2;

// Routes that share a view bar. A family is the first path segment, except
// that /scouting's tools are peers of it rather than children of a group.
function familyOf(route) {
  const path = route.split('?')[0];
  const seg = path.split('/').filter(Boolean)[0];
  return seg ? `/${seg}` : '/';
}

async function measure(page) {
  return page.evaluate(() => {
    const content = document.querySelector('.ps-content');
    if (!content) return null;
    const contentTop = content.getBoundingClientRect().top;

    // The gap between the view bar and the page's first element. Only measured
    // where a bar exists: without one there is nothing to be consistent *with*,
    // and measuring against .ps-content itself returned numbers like -1016 for
    // a page whose first child is a tall scrolled list.
    const bar = document.querySelector('.page-view-bar, .page-view-menu');
    const after = bar ? bar.nextElementSibling : null;
    const startOffset = bar && after
      ? Math.round(after.getBoundingClientRect().top - bar.getBoundingClientRect().bottom)
      : null;

    const scrollers = [...content.querySelectorAll('*')]
      .filter((el) => {
        const s = getComputedStyle(el);
        return /auto|scroll/.test(s.overflowY) && el.scrollHeight > el.clientHeight + 4;
      })
      .map((el) => String(el.className || el.tagName).trim().split(/\s+/)[0])
      .filter(Boolean)
      .sort();

    // The browser's own naming order: aria-label, then aria-labelledby, then
    // the label element, then text. Same order guard 1 uses.
    const nameOf = (el) => {
      const aria = el.getAttribute('aria-label');
      if (aria) return aria.trim();
      const by = el.getAttribute('aria-labelledby');
      if (by) {
        const target = document.getElementById(by);
        if (target?.textContent) return target.textContent.trim();
      }
      return (el.textContent || '').replace(/\s+/g, ' ').trim();
    };
    const counts = new Map();
    for (const el of document.querySelectorAll('.ps-content button, .ps-content a, .ps-sidebar a, header button')) {
      const r = el.getBoundingClientRect();
      if (!r.width || !r.height) continue;
      const name = nameOf(el);
      if (!name || name.length > 48) continue;
      counts.set(name, (counts.get(name) || 0) + 1);
    }
    const duplicates = [...counts.entries()]
      .filter(([, n]) => n > 1)
      .map(([name, n]) => `${name} x${n}`)
      .sort();

    /* The gap between sibling cards. One role, and it was 16px on nineteen
       routes, 12px on three, and 10px, 11px and 14px on Live Scouting alone —
       .center-main was declared in three files with three different values.
       A number picked per page is how that happens; --card-stack-gap is the
       role, and this is what stops it drifting back. */
    const cardGaps = new Set();
    const holders = new Set();
    for (const card of document.querySelectorAll('.surface-card')) {
      if (card.parentElement) holders.add(card.parentElement);
    }
    for (const holder of holders) {
      if (holder.querySelectorAll(':scope > .surface-card').length < 2) continue;
      const s = getComputedStyle(holder);
      if (!/grid|flex/.test(s.display)) continue;
      cardGaps.add(s.rowGap);
    }

    /* Every internal destination and what this route calls it. One place with
       two names is the "four buttons that do the same thing" feeling: the same
       page was Compare and Open Builder, Events and Event Center, and the
       alliance builder had four names across two pages that call the same
       endpoint. */
    const destinations = {};
    for (const el of document.querySelectorAll('a[href]')) {
      const r = el.getBoundingClientRect();
      if (!r.width || !r.height) continue;
      const href = el.getAttribute('href') || '';
      if (!href || href.startsWith('http') || href.startsWith('mailto')) continue;
      const dest = href.replace(/^#/, '').split('?')[0];
      const name = nameOf(el).slice(0, 30);
      if (!dest || !name) continue;
      (destinations[dest] ||= []).push(name);
    }
    for (const k of Object.keys(destinations)) destinations[k] = [...new Set(destinations[k])].sort();

    return { startOffset, contentTop: Math.round(contentTop), scrollers, duplicates, destinations, cardGaps: [...cardGaps].sort() };
  });
}

const browser = await chromium.launch();
const context = await newThemedContext(browser, { theme: 'dark', width: 1440, height: 900 });
const page = await context.newPage();

try {
  await page.goto(BASE_URL, { waitUntil: 'domcontentloaded', timeout: 10000 });
} catch {
  console.error(`Guard 9 needs the app running at ${BASE_URL}. Set GUARD_BASE_URL.`);
  process.exit(2);
}

const current = {};
for (const route of ROUTES) {
  if (!await gotoRoute(page, route)) continue;
  await settleContent(page);
  const m = await measure(page);
  if (m) current[route] = m;
}
await browser.close();

if (UPDATE || !existsSync(BASELINE)) {
  writeFileSync(BASELINE, `${JSON.stringify(current, null, 2)}\n`);
  console.log(`Baseline written for ${Object.keys(current).length} routes -> ${relative(ROOT, BASELINE)}`);
  process.exit(0);
}

const baseline = JSON.parse(readFileSync(BASELINE, 'utf8'));
const problems = [];

/* 1. Siblings must line up. */
const families = new Map();
for (const [route, m] of Object.entries(current)) {
  if (m.startOffset === null) continue;
  const f = familyOf(route);
  if (!families.has(f)) families.set(f, []);
  families.get(f).push({ route, offset: m.startOffset });
}
for (const [family, members] of families) {
  if (members.length < 2) continue;
  const offsets = members.map((x) => x.offset);
  const spread = Math.max(...offsets) - Math.min(...offsets);
  if (spread > OFFSET_TOLERANCE_PX) {
    problems.push({
      kind: 'offset',
      detail: `${family} siblings start content ${spread}px apart: `
        + members.map((x) => `${x.route}=${x.offset}px`).join(', '),
    });
  }
}

/* 2. One gap for one role, everywhere. */
const allGaps = new Map();
for (const [route, m] of Object.entries(current)) {
  for (const g of m.cardGaps || []) {
    if (!allGaps.has(g)) allGaps.set(g, []);
    allGaps.get(g).push(route);
  }
}
if (allGaps.size > 1) {
  const summary = [...allGaps.entries()]
    .sort((a, b) => b[1].length - a[1].length)
    .map(([g, routes]) => `${g} on ${routes.length} route(s)${routes.length <= 3 ? ` (${routes.join(', ')})` : ''}`);
  problems.push({ kind: 'card-gap', detail: `stacked cards use ${allGaps.size} different gaps: ${summary.join('; ')}` });
}

/* 3. New scroll containers inside the page. */
for (const [route, m] of Object.entries(current)) {
  const was = new Set(baseline[route]?.scrollers ?? []);
  const gained = m.scrollers.filter((s) => !was.has(s));
  if (gained.length) {
    problems.push({ kind: 'scroller', detail: `${route} gained a nested scroll container: ${[...new Set(gained)].join(', ')}` });
  }
}

/* 4. One destination, one name — across the whole app, not per route. */
const destNames = new Map();
for (const m of Object.values(current)) {
  for (const [dest, names] of Object.entries(m.destinations || {})) {
    if (!destNames.has(dest)) destNames.set(dest, new Set());
    for (const n of names) destNames.get(dest).add(n);
  }
}
const baseDestNames = new Map();
for (const m of Object.values(baseline)) {
  for (const [dest, names] of Object.entries(m.destinations || {})) {
    if (!baseDestNames.has(dest)) baseDestNames.set(dest, new Set());
    for (const n of names) baseDestNames.get(dest).add(n);
  }
}
for (const [dest, names] of destNames) {
  const was = baseDestNames.get(dest)?.size ?? 0;
  if (names.size > 1 && names.size > was) {
    problems.push({ kind: 'name', detail: `${dest} is reachable under ${names.size} names: ${[...names].join('  ·  ')}` });
  }
}

/* 5. More controls sharing a name than before. */
for (const [route, m] of Object.entries(current)) {
  const was = new Set(baseline[route]?.duplicates ?? []);
  const gained = m.duplicates.filter((d) => !was.has(d));
  if (gained.length) {
    problems.push({ kind: 'duplicate', detail: `${route} has controls sharing a name: ${gained.slice(0, 4).join(' · ')}` });
  }
}

console.log('\n' + '─'.repeat(70));
console.log(`Guard 9 — cross-route consistency: ${Object.keys(current).length} routes, ${families.size} view-bar families`);
if (allGaps.size === 1) console.log(`  card-stack gap: ${[...allGaps.keys()][0]} everywhere`);
const multi = [...destNames.entries()].filter(([, n]) => n.size > 1);
console.log(`  ${multi.length} of ${destNames.size} destinations have more than one name`);
for (const [dest, names] of multi) console.log(`      ${dest.padEnd(28)} ${[...names].join('  ·  ')}`);

const improved = [];
for (const [route, m] of Object.entries(current)) {
  const wasDup = baseline[route]?.duplicates?.length ?? 0;
  const wasScroll = baseline[route]?.scrollers?.length ?? 0;
  if (m.duplicates.length < wasDup || m.scrollers.length < wasScroll) {
    improved.push(`${route}  duplicates ${wasDup} -> ${m.duplicates.length}, scrollers ${wasScroll} -> ${m.scrollers.length}`);
  }
}
if (improved.length) {
  console.log(`\n${improved.length} route(s) improved — re-run with --update to lock the gain in:`);
  for (const i of improved) console.log(`  ${i}`);
}

if (!problems.length) {
  console.log('\nSiblings line up, no new nested scrollers, no new shared names. ✓');
  process.exit(0);
}

console.log(`\n${problems.length} INCONSISTENCY(S):\n`);
for (const p of problems) console.log(`  [${p.kind}] ${p.detail}`);
console.log();
console.log('Spacing beneath the view bar belongs to the bar, not to each page wrapper.');
console.log('A nested scroller is usually accidental: `overflow-x: hidden` computes the');
console.log('other axis to `auto`. Use `clip` when you only mean to clip.');
console.log('And a control named the same as its neighbour should name its own region.');
console.log('Spacing that means something — the gap between cards, the padding inside one —');
console.log('has a role token in tokens.css. Reach for the role, not the number.');
process.exit(1);
