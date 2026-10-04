import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { COMPARE_VIEWS, EVENTS_VIEWS, MATCH_HUB_VIEWS, SCOUTING_VIEWS } from './pageViewBarConfig';

describe('page view routes', () => {
  it('only links to routes registered by the app router', () => {
    const routerSource = readFileSync(resolve(process.cwd(), 'src/RootApp.tsx'), 'utf8');
    const views = [...SCOUTING_VIEWS, ...EVENTS_VIEWS, ...COMPARE_VIEWS, ...MATCH_HUB_VIEWS];

    for (const view of views) {
      expect(routerSource, `${view.label} points to missing route ${view.to}`).toContain(`path="${view.to}"`);
    }
  });

  it('preserves selection context within every related page family', () => {
    for (const views of [SCOUTING_VIEWS, EVENTS_VIEWS, COMPARE_VIEWS, MATCH_HUB_VIEWS]) {
      expect(views.every((view) => view.preserveSearch)).toBe(true);
    }
  });

  it('renders each view bar on its family landing page', () => {
    const landingPages = [
      ['src/pages/ScoutingPage.tsx', 'SCOUTING_VIEWS'],
      ['src/pages/EventsPage.tsx', 'EVENTS_VIEWS'],
      ['src/pages/ComparePage.tsx', 'COMPARE_VIEWS'],
      ['src/pages/MatchCenterPage.tsx', 'MATCH_HUB_VIEWS'],
    ] as const;

    for (const [path, configName] of landingPages) {
      const pageSource = readFileSync(resolve(process.cwd(), path), 'utf8');
      expect(pageSource, `${path} does not render ${configName}`).toContain(`items={${configName}}`);
    }
  });
});
