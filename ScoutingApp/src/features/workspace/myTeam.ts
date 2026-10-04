import { getWorkspaceSession } from './workspaceSession';

// "My team" for pages that tailor themselves to one team (Strategy, Auto Paths,
// Data Viz). A team that joined a workspace already told us its number there,
// and that wins: a per-device value used to override it, so two phones on the
// same team could disagree about who "my team" was. The per-device value is for
// people without a workspace. Stored as a plain team key.
export const MY_TEAM_STORAGE_KEY = 'scouting_manual_my_team_v1';

export function workspaceTeamKey(): string {
  const number = getWorkspaceSession()?.workspace.frc_team_number;
  return typeof number === 'number' && number > 0 ? `frc${number}` : '';
}

export function readMyTeamKey(): string {
  const fromWorkspace = workspaceTeamKey();
  if (fromWorkspace) return fromWorkspace;
  let stored: string;
  try {
    stored = (window.localStorage.getItem(MY_TEAM_STORAGE_KEY) || '').trim().toLowerCase();
  } catch {
    stored = '';
  }
  if (!stored) return '';
  return stored.startsWith('frc') ? stored : `frc${stored.replace(/\D/g, '')}`;
}

// Only people without a workspace keep a per-device choice; a workspace
// member who types another team is looking at it for this visit.
export function saveMyTeamKey(teamKey: string): void {
  if (workspaceTeamKey()) return;
  try {
    if (teamKey) window.localStorage.setItem(MY_TEAM_STORAGE_KEY, teamKey);
    else window.localStorage.removeItem(MY_TEAM_STORAGE_KEY);
  } catch {
    // storage unavailable; the choice lasts for this visit
  }
}
