import type { TutorialScope } from './userSettings';

// Each tour belongs to one page. A page without its own tour gets none: falling back to
// Home's tour on, say, Predictions pointed every step at things that weren't there.
const SCOPE_BY_PATH: Record<string, TutorialScope> = {
  '/home': 'home',
  '/events': 'events',
  '/scouting': 'scouting',
  '/scouting/pit': 'pit-scouting',
  '/scouting/assignments': 'assignments',
  '/match-center': 'match-center',
  '/match-center/predictions': 'predictions',
  '/match-center/strategy': 'strategy',
  '/team-center': 'team-center',
  '/compare': 'compare',
  '/compare/alliance-advisor': 'alliance-advisor',
  '/compare/picklist': 'picklist',
  '/favorites': 'favorites',
  '/settings': 'settings',
  '/my-team': 'my-team',
};

export function resolveScopeFromPath(pathname: string): TutorialScope | null {
  const path = String(pathname || '').trim().toLowerCase().replace(/\/+$/, '') || '/home';
  return SCOPE_BY_PATH[path] ?? null;
}
