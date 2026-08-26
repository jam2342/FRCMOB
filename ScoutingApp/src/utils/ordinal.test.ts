import { describe, expect, it } from 'vitest';
import { ordinal } from './ordinal';

describe('ordinal', () => {
  it('handles the four regular endings', () => {
    expect([1, 2, 3, 4].map(ordinal)).toEqual(['1st', '2nd', '3rd', '4th']);
  });

  it('handles the teens, which is the only reason this is a function', () => {
    expect([11, 12, 13].map(ordinal)).toEqual(['11th', '12th', '13th']);
    expect([111, 112, 113].map(ordinal)).toEqual(['111th', '112th', '113th']);
  });

  it('resumes the regular pattern past the teens', () => {
    expect([21, 22, 23, 101, 102].map(ordinal)).toEqual(['21st', '22nd', '23rd', '101st', '102nd']);
  });

  it('does not invent a fraction', () => {
    expect(ordinal(4.7)).toBe('4th');
  });
});
