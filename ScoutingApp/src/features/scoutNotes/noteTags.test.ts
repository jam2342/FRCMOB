import { describe, expect, it } from 'vitest';
import { normalizeNoteTags, noteTagLabel, toggleNoteTag } from './noteTags';

describe('scout note tags', () => {
  it('keeps known tags once, in list order, however a saved report spelled them', () => {
    expect(normalizeNoteTags(['BROKE_DOWN', 'defense', 'defense', 'made_up', 7, null])).toEqual(['defense', 'broke_down']);
    expect(normalizeNoteTags('defense')).toEqual([]);
    expect(normalizeNoteTags(undefined)).toEqual([]);
  });

  it('toggles a tag on and off', () => {
    expect(toggleNoteTag([], 'tipped')).toEqual(['tipped']);
    expect(toggleNoteTag(['defense', 'tipped'], 'defense')).toEqual(['tipped']);
  });

  it('labels tags for people', () => {
    expect(noteTagLabel('great_driver')).toBe('Great driver');
    expect(noteTagLabel('unknown')).toBe('unknown');
  });
});
