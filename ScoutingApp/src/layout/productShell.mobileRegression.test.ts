import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const cssPath = resolve(process.cwd(), 'src', 'layout', 'ProductShell.css');
const css = readFileSync(cssPath, 'utf8');

describe('ProductShell mobile regressions', () => {
  it('keeps Event Finder submit control compact on mobile', () => {
    expect(css).toMatch(
      /@media\s*\(max-width:\s*1120px\)\s*\{[\s\S]*?\.center-input-row-event-search\s+\.center-btn\s*\{[\s\S]*?justify-self:\s*end;[\s\S]*?width:\s*auto;[\s\S]*?min-width:\s*116px;[\s\S]*?\}/m,
    );
  });

  it('forces home layout into a single mobile column', () => {
    expect(css).toMatch(
      /@media\s*\(max-width:\s*1120px\)\s*\{[\s\S]*?\.home-fotmob-layout\s*\{[\s\S]*?grid-template-columns:\s*1fr;[\s\S]*?\}/m,
    );
  });

  it('preserves shrink-safe desktop home grid to reduce overflow', () => {
    expect(css).toContain('grid-template-columns: minmax(240px, 300px) minmax(0, 1fr) minmax(250px, 320px);');
  });
});
