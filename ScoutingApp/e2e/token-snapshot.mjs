/* Record the resolved value of every custom property, on <html> and on the
 * product shell.
 *
 *   node e2e/token-snapshot.mjs <out.json>
 *
 * Needed because moving a token definition between files is not a text edit —
 * it changes which declaration wins. Custom properties inherit, so a value set
 * on `.product-shell` beats one set on `:root` for everything inside the shell
 * no matter what @layer says: the two declarations apply to different elements,
 * and layers only arbitrate between declarations on the same one.
 *
 * `--motion-fast` is currently declared five times across four files with four
 * different values. The only safe way to consolidate that is to read what the
 * browser actually resolves, write that value down, and prove it unchanged
 * afterwards.
 */
import { chromium } from 'playwright';
import { writeFileSync, readFileSync, readdirSync } from 'node:fs';
import { join, dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { newThemedContext, gotoRoute } from './lib/harness.mjs';

const OUT = process.argv[2];
if (!OUT) { console.error('usage: node e2e/token-snapshot.mjs <out.json>'); process.exit(2); }

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC = resolve(HERE, '..', 'src');

/* Every custom property named anywhere in the stylesheets. getComputedStyle
   will not enumerate custom properties, so the names have to come from source. */
function cssFiles(dir, out = []) {
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, e.name);
    if (e.isDirectory()) cssFiles(p, out);
    else if (e.name.endsWith('.css')) out.push(p);
  }
  return out;
}
const names = new Set();
for (const f of cssFiles(SRC)) {
  for (const m of readFileSync(f, 'utf8').matchAll(/(--[a-zA-Z][\w-]*)\s*:/g)) names.add(m[1]);
}
const NAMES = [...names].sort();
console.log(`${NAMES.length} custom properties named in source`);

const browser = await chromium.launch();
const snapshot = {};
for (const theme of ['dark', 'light']) {
  const context = await newThemedContext(browser, { theme, width: 1440, height: 900 });
  const page = await context.newPage();
  await gotoRoute(page, '/home');
  await page.waitForTimeout(3000);
  snapshot[theme] = await page.evaluate((props) => {
    const read = (el) => {
      if (!el) return null;
      const cs = getComputedStyle(el);
      const out = {};
      for (const p of props) {
        const v = cs.getPropertyValue(p).trim();
        if (v) out[p] = v;
      }
      return out;
    };
    return {
      root: read(document.documentElement),
      shell: read(document.querySelector('.product-shell')),
    };
  }, NAMES);
  const n = Object.keys(snapshot[theme].shell || {}).length;
  console.log(`  ${theme}: ${n} resolved on the shell`);
  await context.close();
}
await browser.close();
writeFileSync(OUT, JSON.stringify(snapshot, null, 2));
console.log(`written: ${OUT}`);
