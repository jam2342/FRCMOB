/* Capture every element's resolved colour properties, per route and theme.
 *
 * A CSS codemod is only safe if you can say which pixels moved. Comparing
 * computed styles beats diffing images: it survives a one-row data change, and
 * when something does differ it names the element and the property instead of
 * handing you a red blob.
 *
 *   node e2e/style-snapshot.mjs <out.json>
 */
import { chromium } from 'playwright';
import { writeFileSync } from 'node:fs';
import { ROUTES, newThemedContext, gotoRoute, SEED_EVENT_KEY, SEED_TEAM_KEY } from './lib/harness.mjs';

const OUT = process.argv[2];
if (!OUT) { console.error('usage: node e2e/style-snapshot.mjs <out.json>'); process.exit(2); }

const PROPS = [
  'color', 'backgroundColor', 'borderTopColor', 'borderRightColor',
  'borderBottomColor', 'borderLeftColor', 'outlineColor', 'boxShadow',
  'fill', 'stroke', 'textDecorationColor', 'caretColor',
  // Type, added for the font-size codemod. The colour pass wanted a diff of
  // zero; the type pass wants movement, but bounded — so these are captured to
  // prove every element moved by at most a pixel rather than to prove nothing
  // moved. style-diff.mjs reads the unit and applies the right rule.
  'fontSize', 'fontWeight',
];

const browser = await chromium.launch();
const snapshot = {};
let elements = 0;

for (const theme of ['dark', 'light']) {
  const context = await newThemedContext(browser, { theme, width: 1440, height: 900 });
  const page = await context.newPage();
  for (const route of ROUTES) {
    const ok = await gotoRoute(page, route);
    if (!ok) continue;
    await page.waitForTimeout(2500);
    const rows = await page.evaluate((props) => {
      // A key that survives re-render but distinguishes siblings: the element's
      // position path plus its tag and classes.
      const keyOf = (el) => {
        const parts = [];
        for (let n = el; n && n !== document.documentElement; n = n.parentElement) {
          const i = n.parentElement ? [...n.parentElement.children].indexOf(n) : 0;
          parts.unshift(`${n.tagName}:${i}`);
        }
        return parts.join('>') + '|' + (el.className || '').toString().slice(0, 80);
      };
      const out = {};
      for (const el of document.querySelectorAll('*')) {
        const cs = getComputedStyle(el);
        const vals = {};
        for (const p of props) {
          const v = cs[p];
          if (v && v !== 'none' && v !== 'rgba(0, 0, 0, 0)' && v !== 'currentcolor') vals[p] = v;
        }
        if (Object.keys(vals).length) out[keyOf(el)] = vals;
      }
      return out;
    }, PROPS);
    snapshot[`${theme} ${route}`] = rows;
    elements += Object.keys(rows).length;
    process.stdout.write(`  ${theme}  ${route.padEnd(28)} ${Object.keys(rows).length}\n`);
  }
  await context.close();
}
await browser.close();
writeFileSync(OUT, JSON.stringify(snapshot));
console.log(`\n${elements} styled elements across ${Object.keys(snapshot).length} route/theme pairs`);
console.log(`seed: event=${SEED_EVENT_KEY || '(none)'} team=${SEED_TEAM_KEY || '(none)'}`);
console.log(`written: ${OUT}`);
