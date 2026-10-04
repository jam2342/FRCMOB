import { chromium } from 'playwright';
import assert from 'node:assert/strict';
const base = process.env.IDENTITY_TEST_URL || 'http://127.0.0.1:4180';
assert.ok(['localhost', '127.0.0.1'].includes(new URL(base).hostname));
const browser = await chromium.launch({ channel: 'chrome' });
try {
const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
const errors = []; page.on('pageerror', e => errors.push(e.message));
await page.goto(`${base}/e2e/identity-preview.html`);
await page.getByText('0 of 24 paths assigned · 0% of recorded points assigned').waitFor();
assert.match(await page.getByRole('combobox', { name: /Assign track/ }).first().getAttribute('aria-label'), /track 24/);
await page.getByRole('checkbox', { name: 'Only unassigned paths' }).check();
await page.getByRole('combobox', { name: 'Assign track 24 to a team' }).selectOption('frc1690');
assert.equal(await page.getByRole('combobox', { name: 'Assign track 24 to a team' }).count(), 0);
await page.getByText('1 of 24 paths assigned · 8% of recorded points assigned').waitFor();
await page.getByRole('combobox', { name: 'Filter paths by bumper colour' }).selectOption('red');
assert.equal(await page.getByRole('combobox', { name: /Assign track/ }).count(), 8);
assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 2));
await page.screenshot({ path: '/tmp/frcmob-identify-phone.png', fullPage: true });
await page.setViewportSize({ width: 1280, height: 900 });
assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 2));
await page.screenshot({ path: '/tmp/frcmob-identify-desktop.png', fullPage: true });
assert.deepEqual(errors, []);
console.log(JSON.stringify({ passed: true, longestFirst: true, assignmentRetainedUnderFilters: true, weightedProgress: true, phoneAndDesktopOverflow: false, pageErrors: errors }));
} finally { await browser.close(); }
