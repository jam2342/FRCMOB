// Shared helpers for the regression checks (see README.md).
import { chromium } from 'playwright';

export const BASE = process.env.REGRESSION_BASE || 'http://127.0.0.1:5210';
export const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

export function launch() {
  // Locally, real Chrome (PW_CHANNEL=chrome); in CI, Playwright's own Chromium.
  return chromium.launch(process.env.PW_CHANNEL ? { channel: process.env.PW_CHANNEL } : {});
}

// A phone-sized context that never sends real telemetry (GPC) and skips the tour.
export async function phone(browser, { width = 390, height = 844, serviceWorkers = 'block', storage = {}, mobile = true } = {}) {
  const ctx = await browser.newContext({
    viewport: { width, height },
    isMobile: mobile,
    hasTouch: mobile,
    serviceWorkers,
  });
  await ctx.addInitScript((extra) => {
    Object.defineProperty(Navigator.prototype, 'globalPrivacyControl', { get: () => true });
    try {
      localStorage.setItem('scouting_tutorial_autoplay', 'false');
      localStorage.setItem('scouting_tour_prompt_dismissed_v1', '1');
      localStorage.setItem('frcmob_tour_prompt_dismissed_v1', '1');
      for (const [key, value] of Object.entries(extra)) if (localStorage.getItem(key) === null) localStorage.setItem(key, value);
    } catch {
      // Storage blocked on purpose by a check.
    }
  }, storage);
  return ctx;
}

export const desktop = (browser, opts = {}) => phone(browser, { width: 1366, height: 900, mobile: false, ...opts });

// Polls until fn returns something truthy; fails with what it last saw.
export async function until(fn, { timeout = 20000, every = 500, what = 'the expected state' } = {}) {
  const end = Date.now() + timeout;
  let last;
  while (Date.now() < end) {
    last = await fn();
    if (last) return last;
    await sleep(every);
  }
  throw new Error(`Timed out after ${timeout / 1000}s waiting for ${what}${last !== undefined ? ` (last: ${JSON.stringify(last).slice(0, 300)})` : ''}`);
}

export function check(condition, message) {
  if (!condition) throw new Error(message);
}

export const bodyText = (page) => page.evaluate(() => document.body.innerText);
export const storedJson = (page, key) => page.evaluate((k) => JSON.parse(localStorage.getItem(k) || '[]'), key);
