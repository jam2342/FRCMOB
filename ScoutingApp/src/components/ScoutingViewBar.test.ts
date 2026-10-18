import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { SCOUTING_VIEWS, scoutingViewTitle } from './pageViewBarConfig';

// The seven scouting pages drifted apart one page at a time: one rendered its menu as a chip row
// on phones, two titled themselves differently from their menu entry, one left its event box
// unlabelled. The browser guard (e2e/guard-consistency.mjs) measures that, but it doesn't run in
// CI, so this reads the pages' source for the same promises.
const SRC = join(dirname(fileURLToPath(import.meta.url)), '..');
const rootApp = readFileSync(join(SRC, 'RootApp.tsx'), 'utf8');

function pageSourceFor(path: string): { name: string; source: string } {
  const route = new RegExp(`path="${path}" element=\\{withPageSuspense\\(<(\\w+) />`).exec(rootApp);
  if (!route) throw new Error(`No route renders ${path}`);
  return { name: route[1], source: readFileSync(join(SRC, 'pages', `${route[1]}.tsx`), 'utf8') };
}

describe('scouting pages share one shape', () => {
  for (const view of SCOUTING_VIEWS) {
    it(`${view.label} uses the shared menu, title and event label`, () => {
      const { name, source } = pageSourceFor(view.to);
      expect(source, `${name} should render <ScoutingViewBar />`).toContain('<ScoutingViewBar />');
      expect(source, `${name} should not build its own menu`).not.toMatch(/<PageViewBar\b/);
      // Live Scouting is the board itself; it has no title card to name.
      if (view.to !== '/scouting') {
        expect(source, `${name} should take its title from the menu`).toContain(`scoutingViewTitle('${view.to}')`);
      }
      for (const picker of source.match(/<EventPicker\b[\s\S]*?\/>/g) ?? []) {
        expect(picker, `${name} has an event box without a visible label`).toMatch(/\blabel="Event"|inputId=/);
      }
    });
  }

  it('names each page after its menu entry', () => {
    expect(scoutingViewTitle('/scouting/record')).toBe('On-Device Breakdown');
    expect(() => scoutingViewTitle('/scouting/nowhere')).toThrow();
  });
});
