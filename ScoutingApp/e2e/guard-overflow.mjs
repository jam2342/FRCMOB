// Guard 4 — no page may scroll sideways on a phone.
//
// Mobile overflow is a recurring regression in this codebase and it is trivially
// detectable: if the document is wider than the viewport, something overflowed.
// When it fails, report the widest offending elements so the fix is obvious.
//
//   node e2e/guard-overflow.mjs
//
// Exits nonzero if any route overflows.

import { chromium } from 'playwright';

import { ROUTES, assertServerUp, gotoRoute, newThemedContext, settleContent } from './lib/harness.mjs';

const VIEWPORTS = [
  { name: '320px', width: 320, height: 720 },
  { name: '390px', width: 390, height: 844 },
];
const THEMES = ['dark', 'light'];

// A couple of px of slack keeps sub-pixel rounding from crying wolf.
const TOLERANCE_PX = 2;

async function findOffenders(page) {
  return page.evaluate(() => {
    const limit = document.documentElement.clientWidth;
    const offenders = [];
    for (const el of document.querySelectorAll('*')) {
      const rect = el.getBoundingClientRect();
      if (rect.width === 0 && rect.height === 0) continue;
      const overhang = Math.round(rect.right - limit);
      if (overhang <= 2) continue;
      const cls = typeof el.className === 'string' ? el.className.trim().split(/\s+/).slice(0, 3).join('.') : '';
      offenders.push({
        tag: el.tagName.toLowerCase(),
        cls,
        overhang,
        width: Math.round(rect.width),
        text: (el.textContent || '').trim().slice(0, 40),
      });
    }
    // Widest overhang first; keep it short enough to act on.
    offenders.sort((a, b) => b.overhang - a.overhang);
    return offenders.slice(0, 5);
  });
}

async function main() {
  await assertServerUp();
  const browser = await chromium.launch();
  const failures = [];
  let checked = 0;

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
        if (!loaded) {
          console.log(`  ?  ${theme}/${viewport.name}  ${route}  (navigation failed, skipped)`);
          continue;
        }
        checked += 1;

        const { scrollWidth, clientWidth } = await page.evaluate(() => ({
          scrollWidth: document.documentElement.scrollWidth,
          clientWidth: document.documentElement.clientWidth,
        }));
        const overflow = scrollWidth - clientWidth;

        if (overflow > TOLERANCE_PX) {
          const offenders = await findOffenders(page);
          failures.push({ theme, viewport: viewport.name, route, overflow, offenders });
          console.log(`  ✗  ${theme}/${viewport.name}  ${route}  +${overflow}px`);
        }
      }
      await context.close();
    }
  }
  await browser.close();

  console.log(`\n${'─'.repeat(64)}`);
  console.log(`Guard 4 — mobile overflow: ${checked} page renders checked`);

  if (failures.length === 0) {
    console.log('No horizontal overflow. ✓\n');
    return;
  }

  console.log(`${failures.length} overflowing render(s):\n`);
  for (const failure of failures) {
    console.log(`  ${failure.route}  [${failure.theme} · ${failure.viewport}]  overflows by ${failure.overflow}px`);
    for (const offender of failure.offenders) {
      const selector = offender.cls ? `${offender.tag}.${offender.cls}` : offender.tag;
      console.log(`      +${String(offender.overhang).padStart(4)}px  ${selector}  (w=${offender.width})  "${offender.text}"`);
    }
    console.log('');
  }
  process.exitCode = 1;
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
