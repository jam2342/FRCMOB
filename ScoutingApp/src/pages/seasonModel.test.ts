import { describe, expect, it } from 'vitest';
import { favouriteRangeLabel, modelNoteText } from './seasonModel';

describe('modelNoteText', () => {
  it('names the update date and the matches it was tuned on', () => {
    expect(modelNoteText({ version: 3, updated_at: '2027-03-08T10:00:00', fitted_through: null, matches: 6214, provisional: false }, 'en-GB'))
      .toBe('Model updated 8 Mar · tuned on 6,214 matches');
    expect(modelNoteText({ version: 3, updated_at: null, fitted_through: null, matches: 1, provisional: false }, 'en-GB')).toBe('Model tuned on 1 match');
  });
  it('says plainly when the season is still on the preseason model', () => {
    expect(modelNoteText({ version: 1, updated_at: null, fitted_through: null, matches: 0, provisional: true })).toMatch(/^Preseason model/);
  });
});

describe('favouriteRangeLabel', () => {
  it('labels the top bin as ending at 100%', () => {
    expect(favouriteRangeLabel({ favourite_from: 0.9, favourite_to: 1.0001, matches: 1, predicted: 0.93, won: 1 })).toBe('90–100%');
  });
});
