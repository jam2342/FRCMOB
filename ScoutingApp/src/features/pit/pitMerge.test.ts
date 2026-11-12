import { expect, it } from 'vitest';
import { buildChanges, formatAnswer, mergeTeammate, sameAnswer, touchedFields } from './pitMerge';

it('treats a missing answer and null as the same, and compares lists by content', () => {
  expect(sameAnswer(undefined, null)).toBe(true);
  expect(sameAnswer(null, 0)).toBe(false);
  expect(sameAnswer(['a', 'b'], ['a', 'b'])).toBe(true);
  expect(sameAnswer(['a'], ['b'])).toBe(false);
  expect(sameAnswer(false, undefined)).toBe(false);
});

it('sends only what changed, with "from" and "to" left out where there was no answer', () => {
  const base = { drivetrain: 'Swerve', weight_lbs: 110, notes: 'old' };
  const form = { drivetrain: 'Swerve', weight_lbs: 112, vision_system: true };
  expect(buildChanges(base, form)).toEqual({
    weight_lbs: { from: 110, to: 112 },
    notes: { from: 'old' },
    vision_system: { to: true },
  });
  // A blank number (null) over no answer is no change.
  expect(buildChanges({}, { weight_lbs: null })).toEqual({});
});

it('can force a field that did not change, carrying the server value it replaces', () => {
  expect(buildChanges({ drivetrain: 'Tank / KOP' }, { drivetrain: 'Tank / KOP' }, { drivetrain: 'Swerve' })).toEqual({
    drivetrain: { from: 'Swerve', to: 'Tank / KOP' },
  });
});

it("takes a teammate's answers for untouched fields and keeps this scout's edits", () => {
  const base = { drivetrain: 'Swerve', weight_lbs: 110 };
  const form = { drivetrain: 'Swerve', weight_lbs: 112, notes: 'mine' };
  const server = { drivetrain: 'Mecanum', weight_lbs: 110, claimed_climb: 'Level 2' };
  const merged = mergeTeammate(base, form, server);
  expect(merged.form).toEqual({ drivetrain: 'Mecanum', weight_lbs: 112, notes: 'mine', claimed_climb: 'Level 2' });
  // The base follows the server except where this scout edited, so a save still sends those edits.
  expect(merged.base).toEqual({ drivetrain: 'Mecanum', weight_lbs: 110, claimed_climb: 'Level 2' });
  expect(merged.clashes).toEqual([]);
  expect(touchedFields(merged.base, merged.form).sort()).toEqual(['notes', 'weight_lbs']);
});

it('names fields both changed differently, but not ones both changed the same way', () => {
  const merged = mergeTeammate({ weight_lbs: 110, drivetrain: 'Swerve' }, { weight_lbs: 112, drivetrain: 'Tank / KOP' }, { weight_lbs: 115, drivetrain: 'Tank / KOP' });
  expect(merged.clashes).toEqual(['weight_lbs']);
  expect(merged.form).toEqual({ weight_lbs: 112, drivetrain: 'Tank / KOP' });
});

it('reads answers the way the form shows them', () => {
  expect(formatAnswer('weight_lbs', 112)).toBe('112 lbs');
  expect(formatAnswer('vision_system', false)).toBe('No');
  expect(formatAnswer('auto_capabilities', ['Mobility', 'Scores preload'])).toBe('Mobility, Scores preload');
  expect(formatAnswer('drivetrain', null)).toBe('no answer');
});
