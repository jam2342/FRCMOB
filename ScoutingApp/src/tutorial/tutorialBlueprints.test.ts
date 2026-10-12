import { expect, it } from 'vitest';
import { getTutorialBlueprint } from './tutorialBlueprints';

it('teaches current Team Center and Compare controls', () => {
  const team = getTutorialBlueprint('team-center').walkthrough;
  const compare = getTutorialBlueprint('compare').walkthrough;
  expect(team.map(step => step.description).join(' ')).not.toMatch(/EPA|raw payload|advanced tab/i);
  const tabs = compare.find(step => step.id === 'compare-tabs');
  expect(tabs?.description).toContain('Summary');
  expect(tabs?.description).toContain('Detailed');
  expect(compare.find(step => step.id === 'compare-alliance-advisor')?.description).toContain('Alliance Advisor');
  expect(compare.map(step => step.description).join(' ')).not.toMatch(/diagnostics tab|theoretical tab|builder mode/i);
});
