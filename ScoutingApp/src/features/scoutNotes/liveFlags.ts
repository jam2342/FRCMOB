import { matchLabelFromKey } from '../../utils/matchLabel';
import { isScoutNoteTag, noteTagLabel, WARNING_NOTE_TAGS, type ScoutNoteTag } from './noteTags';

// The tags a phone offers on the match board itself, mid-match. The rest wait for Review.
export const LIVE_FLAG_TAGS: readonly ScoutNoteTag[] = ['broke_down', 'tipped', 'defense', 'great_driver'];

export type LiveFlag = { scout: string; teamKey: string; matchKey: string; tag: ScoutNoteTag };

export function liveFlagRequest(tag: ScoutNoteTag, teamKey: string, matchKey: string): string | null {
  if (!teamKey || !matchKey) return null;
  return JSON.stringify({ type: 'flag', tag, team_key: teamKey, match_key: matchKey });
}

export function parseLiveFlag(payload: Record<string, unknown>): LiveFlag | null {
  const tag = String(payload.tag ?? '');
  const teamKey = String(payload.team_key ?? '').toLowerCase();
  const matchKey = String(payload.match_key ?? '').toLowerCase();
  const scout = String(payload.scout_profile ?? '').trim();
  if (!isScoutNoteTag(tag) || !/^frc\d+$/.test(teamKey) || !matchKey || !scout) return null;
  return { scout, teamKey, matchKey, tag };
}

// "Ava: 254 · Broke down · QM 12". The flagger never hears their own flag back.
export function liveFlagToast(flag: LiveFlag, me: string): { message: string; tone: 'warning' | 'info' } | null {
  if (flag.scout.toLowerCase() === me.trim().toLowerCase()) return null;
  const match = matchLabelFromKey(flag.matchKey) ?? flag.matchKey;
  return {
    message: `${flag.scout}: ${flag.teamKey.replace(/^frc/, '')} · ${noteTagLabel(flag.tag)} · ${match}`,
    tone: WARNING_NOTE_TAGS.has(flag.tag) ? 'warning' : 'info',
  };
}
