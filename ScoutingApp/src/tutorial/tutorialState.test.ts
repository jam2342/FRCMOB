import { beforeEach, describe, expect, it } from 'vitest';
import {
  clearTutorialChecklist,
  clearTutorialSeen,
  countCompletedChecklistItems,
  countSeenTutorials,
  hasSeenTutorial,
  markAllTutorialsSeen,
  isTutorialChecklistItemChecked,
  markTutorialSeen,
  readTutorialChecklistScope,
  readTutorialSeenMap,
  setTutorialChecklistItem,
} from './tutorialState';

describe('tutorialState', () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it('tracks seen tutorials by scope', () => {
    expect(hasSeenTutorial('home')).toBe(false);
    markTutorialSeen('home', 12345);
    expect(hasSeenTutorial('home')).toBe(true);
    expect(readTutorialSeenMap().home).toBe(12345);
    expect(countSeenTutorials()).toBe(1);
  });

  it('marks every tutorial seen at once', () => {
    markAllTutorialsSeen(54321);
    expect(countSeenTutorials()).toBeGreaterThan(1);
    expect(readTutorialSeenMap().home).toBe(54321);
    expect(readTutorialSeenMap().settings).toBe(54321);
  });

  it('clears one scope or all scopes', () => {
    markTutorialSeen('home', 1);
    markTutorialSeen('events', 2);
    clearTutorialSeen('home');
    expect(hasSeenTutorial('home')).toBe(false);
    expect(hasSeenTutorial('events')).toBe(true);
    clearTutorialSeen();
    expect(countSeenTutorials()).toBe(0);
  });

  it('tracks checklist completion per scope', () => {
    expect(countCompletedChecklistItems('home', ['a', 'b'])).toBe(0);
    setTutorialChecklistItem('home', 'a', true, 111);
    setTutorialChecklistItem('home', 'b', true, 222);
    expect(isTutorialChecklistItemChecked('home', 'a')).toBe(true);
    expect(readTutorialChecklistScope('home').a).toBe(111);
    expect(countCompletedChecklistItems('home', ['a', 'b', 'c'])).toBe(2);
    setTutorialChecklistItem('home', 'a', false);
    expect(isTutorialChecklistItemChecked('home', 'a')).toBe(false);
    expect(countCompletedChecklistItems('home', ['a', 'b', 'c'])).toBe(1);
  });

  it('clears checklist state by scope or globally', () => {
    setTutorialChecklistItem('home', 'h1', true, 1);
    setTutorialChecklistItem('events', 'e1', true, 2);
    clearTutorialChecklist('home');
    expect(readTutorialChecklistScope('home')).toEqual({});
    expect(isTutorialChecklistItemChecked('events', 'e1')).toBe(true);
    clearTutorialChecklist();
    expect(readTutorialChecklistScope('events')).toEqual({});
  });
});
