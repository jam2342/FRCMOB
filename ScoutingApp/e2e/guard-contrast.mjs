// Guard 3 — text must stay readable in BOTH themes.
//
// This is the guard that catches the bug class already shipping: a colour that
// works in one theme and disappears in the other. It would have caught the
// invisible light-theme card titles the day they landed.
//
// The browser only reports colour strings. Every calculation happens in
// e2e/lib/contrast.mjs, which is unit-tested — see contrast.test.mjs for why.
//
//   node e2e/guard-contrast.mjs
//
// Exits nonzero if any text fails WCAG AA for its size.

import { chromium } from 'playwright';

import { evaluateSample, parseCssColor } from './lib/contrast.mjs';
import { ROUTES, assertServerUp, gotoRoute, newThemedContext, settleContent } from './lib/harness.mjs';

const THEMES = ['dark', 'light'];

// Desktop AND mobile. Several components only mount below the mobile
// breakpoint — the bottom tab bar, the mobile search overlay — so a
// desktop-only sweep silently never measures them. That blind spot hid
// --color-text-dim sitting at 2.6:1 on 10px tab labels.
const VIEWPORTS = [
  { name: 'desktop', width: 1440, height: 900 },
  { name: 'mobile', width: 390, height: 844 },
];

// Collect every element that directly renders visible text, plus the stack of
// background colours behind it. Returns strings only — no maths in here.
async function collectSamples(page) {
  return page.evaluate(() => {
    const samples = [];

    const hasOwnText = (el) => {
      for (const node of el.childNodes) {
        if (node.nodeType === Node.TEXT_NODE && node.textContent.trim()) return true;
      }
      return false;
    };

    for (const el of document.body.querySelectorAll('*')) {
      if (!hasOwnText(el)) continue;

      const style = getComputedStyle(el);
      if (style.display === 'none' || style.visibility === 'hidden') continue;
      if (Number.parseFloat(style.opacity) === 0) continue;

      const rect = el.getBoundingClientRect();
      if (rect.width === 0 || rect.height === 0) continue;

      // Walk ancestors collecting backgrounds until something is opaque.
      const backgroundLayers = [];
      let node = el;
      while (node && node !== document.documentElement.parentElement) {
        const bg = getComputedStyle(node).backgroundColor;
        backgroundLayers.push(bg);
        if (/^rgb\(/.test(bg)) break; // opaque, stop climbing
        const alphaMatch = bg.match(/^rgba\([^)]*,\s*([\d.]+)\s*\)$/);
        if (alphaMatch && Number.parseFloat(alphaMatch[1]) === 1) break;
        node = node.parentElement;
      }

      const cls = typeof el.className === 'string'
        ? el.className.trim().split(/\s+/).slice(0, 2).join('.')
        : '';

      samples.push({
        color: style.color,
        backgroundLayers,
        fontSize: style.fontSize,
        fontWeight: style.fontWeight,
        tag: el.tagName.toLowerCase(),
        cls,
        text: el.textContent.trim().slice(0, 42),
      });
    }

    return {
      samples,
      pageBackground: getComputedStyle(document.body).backgroundColor,
    };
  });
}

async function main() {
  await assertServerUp();
  const browser = await chromium.launch();

  // Unique (colour on background at size) combinations — one CSS rule usually
  // produces hundreds of elements, and we want the rule, not the elements.
  const violations = new Map();
  let samplesChecked = 0;
  let skipped = 0;
  const skippedDetail = [];

  for (const theme of THEMES) {
   for (const viewport of VIEWPORTS) {
    const context = await newThemedContext(browser, { theme, ...viewport });
    const page = await context.newPage();

    for (const route of ROUTES) {
      const loaded = await gotoRoute(page, route);
      // Measuring before the page has finished rendering makes a baseline
      // churn for no reason: this guard reported three Alliance Advisor selects
      // as lost, then present, then lost, purely on load timing. Guards 7, 8
      // and 9 already do this — these three predate it.
      if (loaded) await settleContent(page);
      if (!loaded) continue;

      const { samples, pageBackground } = await collectSamples(page);
      // Fall back to white only if the page itself declares nothing.
      const base = parseCssColor(pageBackground) || { r: 255, g: 255, b: 255, a: 1 };
      const opaqueBase = base.a === 1 ? base : { r: 255, g: 255, b: 255, a: 1 };

      for (const sample of samples) {
        samplesChecked += 1;
        const verdict = evaluateSample(sample, opaqueBase);
        if (verdict.skipped) {
          skipped += 1;
          // Name what was skipped. A silently unmeasured element is the same
          // class of blind spot as the guards measuring one page 23 times.
          const where = sample.cls ? `${sample.tag}.${sample.cls}` : sample.tag;
          skippedDetail.push(`${verdict.skipped} · ${route} [${theme}] ${where} · ${verdict.color ?? ''}`);
          continue;
        }
        if (verdict.ok) continue;

        const selector = sample.cls ? `${sample.tag}.${sample.cls}` : sample.tag;
        const key = `${theme}|${viewport.name}|${selector}|${sample.color}|${Math.round(verdict.ratio * 100)}`;
        if (!violations.has(key)) {
          violations.set(key, {
            theme, viewport: viewport.name, selector, route,
            color: sample.color,
            ratio: verdict.ratio,
            required: verdict.required,
            text: sample.text,
            count: 0,
            routes: new Set(),
          });
        }
        const entry = violations.get(key);
        entry.count += 1;
        entry.routes.add(route);
      }
    }
    await context.close();
   }
  }
  await browser.close();

  const found = [...violations.values()].sort((a, b) => a.ratio - b.ratio);

  console.log(`\n${'─'.repeat(70)}`);
  console.log(
    `Guard 3 — theme contrast: ${samplesChecked} text samples · `
    + `${ROUTES.length} routes x ${THEMES.length} themes x ${VIEWPORTS.length} viewports`,
  );
  if (skipped) {
    console.log(`(${skipped} skipped — colour format not expressible as rgb)`);
    for (const line of [...new Set(skippedDetail)].slice(0, 10)) console.log(`    ${line}`);
  }

  if (found.length === 0) {
    console.log('All text meets WCAG AA for its size. ✓\n');
    return;
  }

  console.log(`\n${found.length} distinct failing colour/selector combinations:\n`);
  const worst = found.slice(0, 40);
  for (const v of worst) {
    const ratio = v.ratio.toFixed(2).padStart(6);
    const severity = v.ratio < 1.5 ? 'INVISIBLE' : v.ratio < 3 ? 'severe   ' : 'below AA ';
    console.log(`  ${ratio}:1  need ${v.required}  [${v.theme}/${v.viewport}] ${severity}  ${v.selector}`);
    console.log(`           ${v.color}  ·  ${v.routes.size} route(s)  ·  "${v.text}"`);
  }
  if (found.length > worst.length) {
    console.log(`\n  … and ${found.length - worst.length} more.`);
  }
  console.log('');
  process.exitCode = 1;
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
