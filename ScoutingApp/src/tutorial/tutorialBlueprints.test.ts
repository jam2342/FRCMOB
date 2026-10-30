import { readdirSync, readFileSync, statSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { resolveScopeFromPath } from '../layout/appUx';
import { TUTORIAL_SCOPES } from '../layout/userSettings';
import { allTutorialBlueprints, getTutorialBlueprint } from './tutorialBlueprints';

// The tours once pointed at class names that later clean-ups renamed or removed, so most steps
// floated over nothing and described features that were gone. Steps now point at data-tour
// hooks; this reads the source to make sure every hook a tour uses is still rendered somewhere.
const SRC = join(dirname(fileURLToPath(import.meta.url)), '..');

function sourceFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return sourceFiles(path);
    // *.testkit.* files are shared test setup, not pages.
    return /\.tsx?$/.test(name) && !/\.(test|testkit)\.tsx?$/.test(name) ? [path] : [];
  });
}

const source = sourceFiles(SRC).map((path) => readFileSync(path, 'utf8')).join('\n');
const steps = allTutorialBlueprints().flatMap((blueprint) => blueprint.walkthrough.map((step) => ({ scope: blueprint.scope, step })));

function selectorsOf(step: (typeof steps)[number]['step']): string[] {
  return [step.selector, step.mobileSelector].filter((s): s is string => Boolean(s));
}

describe('page tours', () => {
  it('has a tour for every scope', () => {
    for (const scope of TUTORIAL_SCOPES) {
      const blueprint = getTutorialBlueprint(scope);
      expect(blueprint.scope).toBe(scope);
      expect(blueprint.walkthrough.length).toBeGreaterThan(0);
    }
  });

  it('points only at hooks and ids the app renders', () => {
    const missing: string[] = [];
    for (const { scope, step } of steps) {
      for (const selector of selectorsOf(step)) {
        for (const [, hook] of selector.matchAll(/\[data-tour="([^"]+)"\]/g)) {
          if (!source.includes(`data-tour="${hook}"`) && !source.includes(`tourId="${hook}"`)) missing.push(`${scope}/${step.id}: ${hook}`);
        }
        for (const [, id] of selector.matchAll(/#([\w-]+)/g)) {
          if (!source.includes(`id="${id}"`)) missing.push(`${scope}/${step.id}: #${id}`);
        }
      }
    }
    expect(missing).toEqual([]);
  });

  it('keeps steps short and unique within each tour', () => {
    for (const blueprint of allTutorialBlueprints()) {
      const ids = blueprint.walkthrough.map((step) => step.id);
      expect(new Set(ids).size, blueprint.scope).toBe(ids.length);
    }
    for (const { step } of steps) {
      expect(step.title.length, step.id).toBeLessThanOrEqual(48);
      expect(step.description.length, step.id).toBeLessThanOrEqual(200);
      expect((step.tip ?? '').length, step.id).toBeLessThanOrEqual(110);
      expect(selectorsOf(step).length, step.id).toBeGreaterThan(0);
    }
  });

  it('does not describe features that are gone', () => {
    const text = steps.map(({ step }) => `${step.title} ${step.description} ${step.tip ?? ''}`).join(' ');
    expect(text).not.toMatch(/\b(EPA|Statbotics|synergy|backfill|queue pressure|room key|weights|context strip|diagnostics)\b/i);
  });
});

describe('resolveScopeFromPath', () => {
  it('gives each page its own tour', () => {
    expect(resolveScopeFromPath('/home')).toBe('home');
    expect(resolveScopeFromPath('/scouting')).toBe('scouting');
    expect(resolveScopeFromPath('/scouting/pit')).toBe('pit-scouting');
    expect(resolveScopeFromPath('/scouting/assignments')).toBe('assignments');
    expect(resolveScopeFromPath('/match-center/predictions')).toBe('predictions');
    expect(resolveScopeFromPath('/compare/picklist')).toBe('picklist');
    expect(resolveScopeFromPath('/my-team/')).toBe('my-team');
  });

  it('offers no tour on pages without one instead of borrowing Home\'s', () => {
    for (const path of ['/events/export', '/events/dashboard', '/scouting/coverage', '/scouting/record', '/privacy', '/terms']) {
      expect(resolveScopeFromPath(path), path).toBeNull();
    }
  });
});
