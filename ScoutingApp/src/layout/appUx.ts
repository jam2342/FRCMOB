import type { TutorialScope } from './userSettings';

export function resolveScopeFromPath(pathname: string): TutorialScope {
  const path = String(pathname || '').trim().toLowerCase();
  if (path.startsWith('/events')) return 'events';
  if (path.startsWith('/scouting')) return 'scouting';
  if (path.startsWith('/match-center')) return 'match-center';
  if (path.startsWith('/team-center')) return 'team-center';
  if (path.startsWith('/compare')) return 'compare';
  if (path.startsWith('/favorites')) return 'favorites';
  if (path.startsWith('/settings')) return 'settings';
  if (path.startsWith('/ops')) return 'ops';
  return 'home';
}
