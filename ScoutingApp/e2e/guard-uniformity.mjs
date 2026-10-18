/* Guard 10 — one look per kind of thing.
 *
 *   node e2e/guard-uniformity.mjs
 *
 * The other guards ask whether a page is readable, fits and is labelled. This one asks whether
 * the same kind of thing looks the same on every page, at phone and at desktop size. Each rule
 * below was broken in several places before it was written (2026-10-18 sweep of every route at
 * 390 and 1366px), always the same way: a page styled its own copy instead of using the shared
 * one, and nothing compared the pages with each other.
 *
 *   text-box     Every text box, search box and select renders with the same height, corner,
 *                border, fill, padding and text size (styles/uniform.css). Favorites' boxes had
 *                16px text beside 13px ones, the phone search was white with an 11px corner and
 *                the rest grey with 6px, the header search was a pill.
 *   tabs         Every tab strip is SegmentedTabs, and every strip that is not the scouting
 *                board looks the same. There were five tab looks: underline strips, boxed pills,
 *                round filter chips (Predictions), Home's own filter row, Coverage's grid.
 *   card-title   Every card title is the same size, weight and letter-spacing. It came in four.
 *   page-title   On phones, the header naming what a page is about (an event, a team) looks the
 *                same: an event's name was 14px and cut to one line, a team's 19px and wrapped.
 *   cut-text     Text cut with "…" or a line clamp carries its full text (a title). Live Ratings'
 *                team names and Compare's team pool were cut with no way to read them.
 *   box-cut      A box that hides overflow is big enough for its content. Rows in lists capped to
 *                the window's height were squeezed and their last line sliced (Home's Today's
 *                Events, the Events finder). Checked on a short laptop window too.
 *   table-fits   A table never scrolls sideways inside its card; the Table primitive turns rows
 *                into cards when the columns don't fit. Four did on a 1366px desktop.
 *   values       A missing value reads "—" and a change never reads "-0.0". Pages said "N/A"
 *                in one place and "—" in the next, and Live Ratings showed "▼ -0.0".
 *
 * The source-side laws (fullscreen only on wide content, "—" for missing values, signedChange,
 * one decimal, SegmentedTabs, EventPicker, EmptyState, MobileViewActions) are in
 * src/consistency.test.ts and run with npm test.
 *
 * Needs the app running against data (GUARD_BASE_URL, default 127.0.0.1:5199). Seeded with
 * GUARD_EVENT_KEY (default 2026arc) so tables, tabs and searches render populated.
 */
import { chromium } from 'playwright';
import { ROUTES, assertServerUp, gotoRoute, newThemedContext, settleContent } from './lib/harness.mjs';

const EVENT = process.env.GUARD_EVENT_KEY || '2026arc';
const TEAM = process.env.GUARD_TEAM_KEY || 'frc254';
const DEEP = [
  `/events?event=${EVENT}`, `/events?event=${EVENT}&tab=schedule`, `/events?event=${EVENT}&tab=teams`,
  `/events?event=${EVENT}&tab=rankings`, `/events?event=${EVENT}&tab=forecast`,
  `/team-center?team=${TEAM}&event=${EVENT}`, `/team-center?team=${TEAM}&event=${EVENT}&tab=performance`,
  `/team-center?team=${TEAM}&event=${EVENT}&tab=events`,
  `/match-center?event=${EVENT}&match=${EVENT}_qm1`, `/match-center/predictions?event=${EVENT}`,
  `/match-center/strategy?event=${EVENT}`, `/compare?event=${EVENT}&teams=${TEAM},frc1678`,
  `/compare/alliance-advisor?event=${EVENT}`, `/scouting?event=${EVENT}&match=${EVENT}_qm1`,
  `/events/dashboard?event=${EVENT}`, `/events/export?event=${EVENT}`, '/my-team',
];
const ONLY = process.env.GUARD_ONLY ? new RegExp(process.env.GUARD_ONLY) : null;
const PAGES = [...ROUTES.filter((route) => !route.startsWith('/primitives')), ...DEEP].filter((route) => !ONLY || ONLY.test(route));
const VIEWPORTS = [
  { name: 'phone', width: 390, height: 844 },
  { name: 'desktop', width: 1366, height: 900 },
  // A short laptop window. Lists capped to the window's height squeezed their rows here, which
  // a 900px-tall check never saw (Home's Today's Events cut the bottom chip off every row).
  { name: 'laptop', width: 1280, height: 640 },
];

// Controls that are a different kind of thing on purpose, each with its own module: the big
// number between a stepper's buttons, the ⌘K palette's borderless line, a colour/range input.
const BARE_CONTROL = '[class*="stepper" i] input, [class*="Stepper" i] input, [role="dialog"] [role="combobox"], [data-control="bare"]';

function measure(BARE_CONTROL) {
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return false;
    const cs = getComputedStyle(el);
    return cs.visibility !== 'hidden' && cs.display !== 'none';
  };
  const where = (el) => {
    const bits = [];
    for (let e = el, i = 0; e && e !== document.body && i < 3; e = e.parentElement, i += 1) {
      const cls = typeof e.className === 'string' ? e.className.trim().split(/\s+/).slice(0, 2).join('.') : '';
      bits.unshift(`${e.tagName.toLowerCase()}${cls ? `.${cls}` : ''}`);
    }
    return bits.join(' > ');
  };
  const out = { boxes: [], tablists: [], tabs: [], titles: [], heroes: [], cut: [], clipped: [], tables: [], values: [] };

  for (const el of document.querySelectorAll('input:not([type=checkbox]):not([type=radio]):not([type=range]):not([type=file]):not([type=color]):not([type=hidden]):not([type=submit]):not([type=button]), select')) {
    if (!visible(el) || el.matches(BARE_CONTROL)) continue;
    const cs = getComputedStyle(el);
    out.boxes.push({ sig: [Math.round(el.getBoundingClientRect().height), cs.borderTopLeftRadius, cs.borderTopWidth, cs.fontSize, cs.paddingLeft].join(' '), at: where(el), name: el.getAttribute('aria-label') || el.placeholder || el.id || '' });
  }

  for (const list of document.querySelectorAll('[role="tablist"]')) {
    if (!visible(list)) continue;
    const board = !list.classList.contains('fm-tab-bar') && !list.classList.contains('center-tabs');
    out.tablists.push({ shared: list.classList.contains('segmented-tabs'), board, at: where(list) });
    if (board) continue;
    for (const tab of list.querySelectorAll('[role="tab"]')) {
      if (!visible(tab)) continue;
      const cs = getComputedStyle(tab);
      out.tabs.push({ sig: [cs.fontSize, cs.fontWeight, cs.borderTopLeftRadius, cs.textTransform].join(' '), at: where(list) });
    }
  }

  for (const h of document.querySelectorAll('.surface-card-head h3')) {
    if (!visible(h)) continue;
    const cs = getComputedStyle(h);
    out.titles.push({ sig: [cs.fontSize, cs.fontWeight, cs.letterSpacing].join(' '), at: where(h), text: h.textContent.trim().slice(0, 30) });
  }

  for (const h of document.querySelectorAll('.fm-event-header-info h2, .fm-team-hero-info h2')) {
    if (!visible(h)) continue;
    const cs = getComputedStyle(h);
    out.heroes.push({ sig: [cs.fontSize, cs.fontWeight, cs.whiteSpace].join(' '), at: where(h), text: h.textContent.trim().slice(0, 30) });
  }

  for (const el of document.querySelectorAll('.ps-content *')) {
    if (!visible(el) || ['INPUT', 'SELECT', 'TEXTAREA'].includes(el.tagName)) continue;
    const cs = getComputedStyle(el);
    const ellipsis = cs.textOverflow === 'ellipsis' && el.scrollWidth > el.clientWidth + 1;
    const clamped = cs.webkitLineClamp && cs.webkitLineClamp !== 'none' && el.scrollHeight > el.clientHeight + 2;
    if (!ellipsis && !clamped) continue;
    const text = (el.textContent || '').trim();
    if (!text) continue;
    // Screen-reader-only text is clipped on purpose.
    if (el.clientWidth <= 1 || cs.clip !== 'auto') continue;
    let named = false;
    for (let e = el, i = 0; e && i < 4; e = e.parentElement, i += 1) {
      if ((e.getAttribute('title') || '').includes(text.slice(0, 12)) || (e.getAttribute('aria-label') || '').includes(text.slice(0, 12))) named = true;
    }
    if (!named) out.cut.push({ text: text.slice(0, 50), at: where(el) });
  }

  // A box that hides its overflow (and is not a scroller or a deliberate "…") must be big
  // enough for what's inside it. Rows in capped lists were squeezed and their last line sliced.
  for (const el of document.querySelectorAll('.ps-content *')) {
    if (!visible(el) || ['INPUT', 'SELECT', 'TEXTAREA', 'svg', 'IMG', 'VIDEO', 'CANVAS', 'IFRAME'].includes(el.tagName)) continue;
    const cs = getComputedStyle(el);
    const clipsY = cs.overflowY === 'hidden' || cs.overflowY === 'clip';
    const clipsX = cs.overflowX === 'hidden' || cs.overflowX === 'clip';
    if (!clipsY && !clipsX) continue;
    if (cs.textOverflow === 'ellipsis' || (cs.webkitLineClamp && cs.webkitLineClamp !== 'none')) continue;
    if (el.clientWidth <= 1 || el.clientHeight <= 1 || cs.clip !== 'auto' || cs.position === 'absolute' && el.clientWidth <= 2) continue;
    // Only content that is really there counts: a visible descendant poking past the box.
    const box = el.getBoundingClientRect();
    const bottom = box.top + el.clientTop + el.clientHeight;
    const right = box.left + el.clientLeft + el.clientWidth;
    const cutChild = [...el.querySelectorAll('*')].find((child) => {
      if (!visible(child) || !(child.textContent || '').trim() && !child.querySelector('svg,img')) return false;
      const r = child.getBoundingClientRect();
      if (r.height < 4 || r.width < 4) return false;
      const overY = clipsY && r.bottom > bottom + 2 && r.top < bottom - 2;
      const overX = clipsX && r.right > right + 2 && r.left < right - 2;
      return (overY || overX) && child.children.length === 0;
    });
    if (cutChild) out.clipped.push({ text: cutChild.textContent.trim().slice(0, 40), at: where(el) });
  }

  for (const table of document.querySelectorAll('.ps-content table')) {
    if (!visible(table)) continue;
    const box = table.parentElement;
    if (box && table.scrollWidth > box.clientWidth + 2) out.tables.push({ at: where(box), need: table.scrollWidth, have: box.clientWidth });
  }

  const text = document.querySelector('.ps-content')?.innerText || '';
  for (const match of text.matchAll(/\bN\/A\b|[−-]0\.0\b(?!\d)/g)) {
    // "N/A" as a scouting answer ("not applicable") is a real option, not a missing value.
    const around = text.slice(Math.max(0, match.index - 24), match.index + 10).replace(/\s+/g, ' ');
    if (/\bN\/A\b/.test(match[0]) && /\b[1-5]\s*N\/A/.test(around)) continue;
    out.values.push({ text: around });
  }
  return out;
}

async function main() {
  await assertServerUp();
  const browser = await chromium.launch({ channel: process.env.GUARD_BROWSER_CHANNEL || undefined });
  const failures = [];
  const signatures = { boxes: new Map(), tabs: new Map(), titles: new Map(), heroes: new Map() };
  let checked = 0;

  for (const viewport of VIEWPORTS) {
    const context = await newThemedContext(browser, { theme: 'light', ...viewport });
    await context.addInitScript(([eventKey]) => {
      localStorage.setItem('scouting_center_event_key', eventKey);
      localStorage.setItem('scouting_tour_prompt_dismissed_v1', '1');
    }, [EVENT]);
    const page = await context.newPage();
    for (const route of PAGES) {
      if (!(await gotoRoute(page, route))) continue;
      await settleContent(page);
      checked += 1;
      const m = await page.evaluate(measure, BARE_CONTROL);
      const at = `${route} [${viewport.name}]`;
      for (const [kind, rows] of [['boxes', m.boxes], ['tabs', m.tabs], ['titles', m.titles], ['heroes', m.heroes]]) {
        for (const row of rows) {
          const key = `${viewport.name} ${row.sig}`;
          if (!signatures[kind].has(key)) signatures[kind].set(key, { viewport: viewport.name, sig: row.sig, count: 0, example: `${at}  ${row.at}${row.name ? `  "${row.name}"` : ''}${row.text ? `  "${row.text}"` : ''}` });
          signatures[kind].get(key).count += 1;
        }
      }
      for (const list of m.tablists) if (!list.shared) failures.push(['tabs', `${at}  a tab strip that is not SegmentedTabs: ${list.at}`]);
      for (const cut of m.cut) failures.push(['cut-text', `${at}  "${cut.text}" is cut with no title  ${cut.at}`]);
      for (const box of m.clipped) failures.push(['box-cut', `${at}  "${box.text}" is sliced by its box  ${box.at}`]);
      for (const table of m.tables) failures.push(['table-fits', `${at}  table needs ${table.need}px in ${table.have}px  ${table.at}`]);
      for (const value of m.values) failures.push(['values', `${at}  "${value.text.trim()}"`]);
    }
    await context.close();
  }
  await browser.close();

  // One look per viewport. The commonest signature is the rule; every other one is an outlier.
  for (const [kind, rule] of [['boxes', 'text-box'], ['tabs', 'tabs'], ['titles', 'card-title'], ['heroes', 'page-title']]) {
    for (const viewport of VIEWPORTS.map((v) => v.name)) {
      const rows = [...signatures[kind].values()].filter((row) => row.viewport === viewport).sort((a, b) => b.count - a.count);
      for (const outlier of rows.slice(1)) {
        failures.push([rule, `${viewport}: ${outlier.count}× "${outlier.sig}" instead of "${rows[0].sig}" — e.g. ${outlier.example}`]);
      }
    }
  }

  console.log(`\n${'─'.repeat(64)}`);
  console.log(`Guard 10 — one look per kind of thing: ${checked} page renders checked`);
  if (failures.length === 0) {
    console.log('Every kind of thing looks the same everywhere. ✓\n');
    return;
  }
  const byRule = new Map();
  for (const [rule, line] of failures) byRule.set(rule, [...(byRule.get(rule) || []), line]);
  for (const [rule, lines] of byRule) {
    console.log(`\n  ${rule} (${lines.length})`);
    for (const line of [...new Set(lines)].slice(0, 25)) console.log(`    ✗ ${line}`);
  }
  console.log('');
  process.exitCode = 1;
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
