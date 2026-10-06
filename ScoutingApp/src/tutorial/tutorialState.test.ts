import { beforeEach, describe, expect, it } from 'vitest';
import {
  clearTutorialChecklist,
  clearTutorialSeen,
  countSeenTutorials,
  hasSeenTutorial,
  markAllTutorialsSeen,
  markTutorialSeen,
} from './tutorialState';

describe('tutorialState', () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it('tracks seen tutorials by scope', () => {
    expect(hasSeenTutorial('home')).toBe(false);
    const seen = markTutorialSeen('home', 12345);
    expect(hasSeenTutorial('home')).toBe(true);
    expect(seen.home).toBe(12345);
    expect(countSeenTutorials()).toBe(1);
  });

  it('marks every tutorial seen at once', () => {
    const seen = markAllTutorialsSeen(54321);
    expect(countSeenTutorials()).toBeGreaterThan(1);
    expect(seen.home).toBe(54321);
    expect(seen.settings).toBe(54321);
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

  it('removes legacy checklist data without clearing seen tutorials', () => {
    localStorage.setItem('scouting_tutorial_checklist_v2', '{"home":{"old":1}}');
    markTutorialSeen('home', 123);
    clearTutorialChecklist();
    expect(localStorage.getItem('scouting_tutorial_checklist_v2')).toBeNull();
    expect(hasSeenTutorial('home')).toBe(true);
  });
});
