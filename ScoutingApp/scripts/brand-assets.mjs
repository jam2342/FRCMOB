// Renders every web and native icon from brand/logo.mjs with Playwright's Chromium.
// Run: npm run brand:assets (outputs are committed; rerun only when the mark changes).
import { chromium } from 'playwright';
import { mkdirSync, readdirSync, existsSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { execFileSync } from 'node:child_process';
import { COLORS, glyph, marks, svg } from '../brand/logo.mjs';

const outputs = [];
const add = (path, inner, width, height = width) => outputs.push({ path, inner, width, height });

writeFileSync('public/icon.svg', svg(marks.tile(), 512) + '\n');
add('public/icons/favicon-32.png', marks.tile(), 32);
add('public/icons/icon-192.png', marks.tile(), 192);
add('public/icons/icon-512.png', marks.tile(), 512);
add('public/icons/icon-maskable-512.png', marks.square(), 512);
add('public/icons/apple-touch-icon.png', marks.square(), 180);
add('public/icons/badge-96.png', marks.badge(), 96);

add('ios/App/App/Assets.xcassets/AppIcon.appiconset/AppIcon-512@2x.png', marks.square(), 1024);
for (const { filename } of JSON.parse(readFileSync('ios/App/App/Assets.xcassets/Splash.imageset/Contents.json')).images) {
  add(`ios/App/App/Assets.xcassets/Splash.imageset/${filename}`, 'splash', 2732);
}
const densities = { mdpi: 48, hdpi: 72, xhdpi: 96, xxhdpi: 144, xxxhdpi: 192 };
for (const [density, size] of Object.entries(densities)) {
  const folder = `android/app/src/main/res/mipmap-${density}`;
  add(join(folder, 'ic_launcher.png'), marks.tile(), size);
  add(join(folder, 'ic_launcher_round.png'), marks.round(), size);
  add(join(folder, 'ic_launcher_foreground.png'), marks.adaptiveForeground(), Math.round(size * 2.25));
}
const res = 'android/app/src/main/res';
for (const folder of readdirSync(res).filter((name) => name.startsWith('drawable'))) {
  const path = join(res, folder, 'splash.png');
  if (!existsSync(path)) continue;
  // Keep each splash's existing dimensions so the density/orientation qualifiers still match.
  const info = execFileSync('sips', ['-g', 'pixelWidth', '-g', 'pixelHeight', path], { encoding: 'utf8' });
  add(path, 'splash', Number(info.match(/pixelWidth: (\d+)/)[1]), Number(info.match(/pixelHeight: (\d+)/)[1]));
}

function splash(width, height) {
  const size = Math.min(width, height) * 0.3;
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${width} ${height}" width="${width}" height="${height}">`
    + `<rect width="${width}" height="${height}" fill="${COLORS.bg}"/>`
    + `<g transform="translate(${(width - size) / 2} ${(height - size) / 2}) scale(${size / 512})">${glyph()}</g></svg>`;
}

// Falls back to installed Google Chrome when Playwright's own browser isn't downloaded.
const browser = await chromium.launch().catch(() => chromium.launch({ channel: 'chrome' }));
const page = await browser.newPage();
for (const { path, inner, width, height } of outputs) {
  mkdirSync(dirname(path), { recursive: true });
  const markup = inner === 'splash' ? splash(width, height) : svg(inner, width, height);
  // Headless Chrome mis-composites very small viewports; render on a larger page and crop.
  await page.setViewportSize({ width: Math.max(width, 800), height: Math.max(height, 800) });
  await page.setContent(`<html><body style="margin:0;background:transparent">${markup}</body></html>`);
  await page.screenshot({ path, omitBackground: true, clip: { x: 0, y: 0, width, height } });
}
await browser.close();
console.log(`Wrote public/icon.svg and ${outputs.length} PNGs from brand/logo.mjs.`);
