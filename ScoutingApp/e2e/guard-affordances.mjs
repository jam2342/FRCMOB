// Guard 1 — no control may disappear during the remodel.
//
// Captures the full set of user-reachable affordances per route (button labels,
// aria-labels, titles, card titles, links, form controls) and compares it to a
// committed baseline. The remodel is a styling migration, so this set should be
// identical before and after; anything that vanishes is a bug, and anything that
// appears was deliberate.
//
//   node e2e/guard-affordances.mjs --update   # write/refresh the baseline
//   node e2e/guard-affordances.mjs            # compare against it
//
// Exits nonzero if any affordance was lost.

import { readFileSync, writeFileSync, existsSync, mkdirSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { chromium } from 'playwright';

import { ROUTES, SEED_EVENT_KEY, assertServerUp, gotoRoute, newThemedContext, settleContent } from './lib/harness.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));

// Seeding an event changes what legitimately renders — a selected match
// replaces "No Match Selected", tables gain rows. That is a different contract,
// not a regression, so each mode keeps its own baseline. Comparing a seeded run
// against the empty-state baseline would report dozens of phantom losses.
const SEEDED = Boolean(SEED_EVENT_KEY);
const BASELINE = resolve(HERE, 'baselines', SEEDED ? 'affordances.seeded.json' : 'affordances.json');
const UPDATE = process.argv.includes('--update');

async function collectAffordances(page) {
  return page.evaluate(() => {
    const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();

    // Connectivity state is the environment, not an affordance. The badge and
    // banner appear only when the API is unreachable, so tracking them makes
    // the baseline depend on whether a backend happened to be running — a
    // difference that says nothing about whether the remodel lost a control.
    const ENVIRONMENTAL = /^(offline|online|reconnecting|you are offline)$/i;

    // Rows rendered from API data are content, not controls. An event picker
    // lists whatever the backend suggests today, so its labels reshuffle
    // between runs — 40+ phantom losses in a single sweep, which drowns the
    // real ones. The remodel cannot "lose" one of these rows; if it lost the
    // list, the card and its heading would disappear and that still registers.
    // `data-guard-data-label` is the explicit form of the same idea: a control
    // whose accessible name IS the record it points at — a team's nickname on a
    // compare chip, say. There is no pattern to normalise a nickname by, and
    // giving the button a generic aria-label would make the page worse for a
    // screen reader to spare the guard. So the markup says so instead.
    const DATA_ROW = '.event-picker-item, .match-picker-item, .events-finder-item, [data-guard-data-label]';

    // Content is not an affordance. Once a backend is attached, event names,
    // team names and match keys flow into buttons and headings, and they change
    // between runs — which made the snapshot unreproducible and every diff
    // meaningless. Collapse anything data-shaped to a placeholder so the guard
    // still answers the only question it is for: did a control disappear?
    const normalise = (s) => s
      .replace(/#?\b\d{4}[a-z]{2,6}\b/gi, '<event>')   // 2026arc, 2026txhou
      .replace(/#\s*\d{1,5}/g, '<team>')                // #1114
      .replace(/\bfrc\d{1,5}\b/gi, '<team>')            // frc1114, in aria-labels
      .replace(/\b(QM|QF|SF|F|Qual|Match)\s*\d+/gi, '<match>')
      .replace(/\b\d[\d.,]*\s*(?:%|pts?|d old|s ago|m ago|teams?|matches?)\b/gi, '<n> $1')
      .replace(/\b\d{2,}\b/g, '<n>')
      // The calendar button names today's date, so the baseline decayed every
      // time the clock passed midnight.
      .replace(/\b(Mon|Tues|Wednes|Thurs|Fri|Satur|Sun)day\b/g, '<day>')
      .replace(/\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\b/g, '<month>')
      // A label that begins with a team number is a link to whichever team the
      // data happened to surface. Its identity is "a team link", not the
      // nickname, which changes with every ranking refresh.
      .replace(/^<team>.*$/, '<team>')
      .replace(/\s+/g, ' ')
      .trim();
    const out = {
      buttons: new Set(),
      ariaLabels: new Set(),
      titles: new Set(),
      headings: new Set(),
      links: new Set(),
      controls: new Set(),
    };

    // Resolve aria-labelledby the way the browser does. Without it a control
    // named that way — every FieldToggle switch — has no name the guard can
    // see, so it silently drops out of the baseline.
    const labelledBy = (el) => {
      const ids = clean(el.getAttribute('aria-labelledby'));
      if (!ids) return '';
      return clean(ids.split(/\s+/).map((id) => document.getElementById(id)?.textContent ?? '').join(' '));
    };

    // aria-label first, exactly as the browser computes an accessible name.
    // Text-first recorded the wrong name for 212 controls, and worse, collapsed
    // distinct ones together: "Collapse sidebar" and "Collapse context bar"
    // both landed in the baseline as "Collapse", so losing one of them would
    // not have registered as a loss at all.
    for (const el of document.querySelectorAll('button, [role="button"]')) {
      if (el.closest(DATA_ROW)) continue;
      const label = clean(el.getAttribute('aria-label')) || labelledBy(el) || clean(el.textContent);
      if (label && !ENVIRONMENTAL.test(label)) out.buttons.add(normalise(label).slice(0, 60));
    }
    for (const el of document.querySelectorAll('[aria-label]')) {
      if (el.closest(DATA_ROW)) continue;
      const v = clean(el.getAttribute('aria-label'));
      if (v && !ENVIRONMENTAL.test(v)) out.ariaLabels.add(normalise(v).slice(0, 60));
    }
    for (const el of document.querySelectorAll('[title]')) {
      if (el.closest(DATA_ROW)) continue;
      const v = clean(el.getAttribute('title'));
      if (v && !ENVIRONMENTAL.test(v)) out.titles.add(normalise(v).slice(0, 60));
    }
    for (const el of document.querySelectorAll('h1, h2, h3, h4')) {
      // An empty state exists precisely when data does not, so its heading is a
      // fact about today's data rather than a control the remodel can lose.
      if (el.closest('.es')) continue;
      const v = clean(el.textContent);
      if (v) out.headings.add(normalise(v).slice(0, 60));
    }
    for (const el of document.querySelectorAll('a[href]')) {
      const v = clean(el.getAttribute('href'));
      if (v && !v.startsWith('http')) out.links.add(v.slice(0, 60));
    }
    // Same ordering for form controls, and a <label for> counts — a visible
    // label is the accessible name just as much as an aria-label is, and
    // migrating a bare input onto a labelled Field should not read as a loss.
    for (const el of document.querySelectorAll('input, select, textarea')) {
      const labelled = el.labels && el.labels[0] ? clean(el.labels[0].textContent) : '';
      const id = clean(el.getAttribute('aria-label')) || labelledBy(el) || labelled
        || clean(el.getAttribute('name')) || clean(el.getAttribute('placeholder'))
        || el.getAttribute('type') || el.tagName.toLowerCase();
      if (id) out.controls.add(`${el.tagName.toLowerCase()}:${id}`.slice(0, 60));
    }

    const result = {};
    for (const [key, set] of Object.entries(out)) result[key] = [...set].sort();
    return result;
  });
}

async function capture() {
  const browser = await chromium.launch();
  // Dark only — affordances don't change by theme, and this halves the runtime.
  const context = await newThemedContext(browser, { theme: 'dark', width: 1440, height: 900 });
  const page = await context.newPage();
  const snapshot = {};

  for (const route of ROUTES) {
    const loaded = await gotoRoute(page, route);
    // Measuring before the page has finished rendering makes a baseline
    // churn for no reason: this guard reported three Alliance Advisor selects
    // as lost, then present, then lost, purely on load timing. Guards 7, 8
    // and 9 already do this — these three predate it.
    if (loaded) await settleContent(page);
    if (!loaded) {
      console.log(`  ?  ${route} (navigation failed, skipped)`);
      continue;
    }
    snapshot[route] = await collectAffordances(page);
    const total = Object.values(snapshot[route]).reduce((n, list) => n + list.length, 0);
    console.log(`  ·  ${route.padEnd(32)} ${String(total).padStart(4)} affordances`);
  }

  await context.close();
  await browser.close();
  return snapshot;
}

function compare(baseline, current) {
  const losses = [];
  const additions = [];

  for (const [route, groups] of Object.entries(baseline)) {
    const now = current[route];
    if (!now) {
      losses.push({ route, kind: 'route', items: ['<entire route missing>'] });
      continue;
    }
    for (const [kind, items] of Object.entries(groups)) {
      const present = new Set(now[kind] || []);
      const missing = items.filter((item) => !present.has(item));
      if (missing.length) losses.push({ route, kind, items: missing });
    }
  }

  for (const [route, groups] of Object.entries(current)) {
    const before = baseline[route];
    if (!before) { additions.push({ route, kind: 'route', items: ['<new route>'] }); continue; }
    for (const [kind, items] of Object.entries(groups)) {
      const had = new Set(before[kind] || []);
      const added = items.filter((item) => !had.has(item));
      if (added.length) additions.push({ route, kind, items: added });
    }
  }

  return { losses, additions };
}

async function main() {
  await assertServerUp();
  const current = await capture();
  const routeCount = Object.keys(current).length;
  const total = Object.values(current)
    .reduce((n, groups) => n + Object.values(groups).reduce((m, l) => m + l.length, 0), 0);

  if (UPDATE || !existsSync(BASELINE)) {
    mkdirSync(dirname(BASELINE), { recursive: true });
    writeFileSync(BASELINE, `${JSON.stringify(current, null, 2)}\n`);
    console.log(`\n${'─'.repeat(64)}`);
    console.log(`Baseline written: ${routeCount} routes, ${total} affordances`);
    console.log(`  ${BASELINE}`);
    console.log('Commit this file — it is the contract the remodel must preserve.\n');
    return;
  }

  const baseline = JSON.parse(readFileSync(BASELINE, 'utf8'));
  const { losses, additions } = compare(baseline, current);

  console.log(`\n${'─'.repeat(64)}`);
  console.log(`Guard 1 — affordance snapshot: ${routeCount} routes, ${total} affordances`);

  if (additions.length) {
    const count = additions.reduce((n, a) => n + a.items.length, 0);
    console.log(`\n${count} added (review, then re-run with --update if intended):`);
    for (const a of additions.slice(0, 12)) {
      console.log(`  + ${a.route} [${a.kind}] ${a.items.slice(0, 4).join(', ')}`);
    }
  }

  if (!losses.length) {
    console.log('\nNothing lost. ✓\n');
    return;
  }

  const count = losses.reduce((n, l) => n + l.items.length, 0);
  console.log(`\n${count} AFFORDANCE(S) LOST:\n`);
  for (const loss of losses) {
    console.log(`  ${loss.route}  [${loss.kind}]`);
    for (const item of loss.items) console.log(`      − ${item}`);
  }
  console.log('');
  process.exitCode = 1;
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
