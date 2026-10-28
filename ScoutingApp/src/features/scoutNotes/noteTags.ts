import type { TeamScoutNote } from '../../api';

// Quick tags a scout taps beside the notes: the things worth knowing about a robot that a
// counter can't hold. The backend keeps the same allow-list (SCOUT_NOTE_TAGS in
// routes_scouting_insights.py) and drops anything else, so add a tag in both places.
export const SCOUT_NOTE_TAGS = [
  { id: 'defense', label: 'Played defense' },
  { id: 'great_driver', label: 'Great driver' },
  { id: 'fast_cycles', label: 'Fast cycles' },
  { id: 'broke_down', label: 'Broke down' },
  { id: 'tipped', label: 'Tipped over' },
  { id: 'lost_power', label: 'Lost power' },
  { id: 'penalties', label: 'Drew penalties' },
  { id: 'no_show', label: 'No show' },
] as const;

export type ScoutNoteTag = (typeof SCOUT_NOTE_TAGS)[number]['id'];

// The ones a strategist wants to hear about the moment they happen, not after the match.
export const WARNING_NOTE_TAGS: ReadonlySet<ScoutNoteTag> = new Set(['broke_down', 'tipped', 'lost_power', 'no_show']);

const TAG_IDS: ReadonlySet<string> = new Set(SCOUT_NOTE_TAGS.map((tag) => tag.id));
const TAG_LABELS = new Map<string, string>(SCOUT_NOTE_TAGS.map((tag) => [tag.id, tag.label]));

export function isScoutNoteTag(value: unknown): value is ScoutNoteTag {
  return typeof value === 'string' && TAG_IDS.has(value);
}

// Known tags only, each once, in the list's order — however a saved report spelled them.
export function normalizeNoteTags(raw: unknown): ScoutNoteTag[] {
  if (!Array.isArray(raw)) return [];
  const present = new Set(raw.map((value) => String(value ?? '').trim().toLowerCase()));
  return SCOUT_NOTE_TAGS.map((tag) => tag.id).filter((id) => present.has(id));
}

export function noteTagLabel(tag: string): string {
  return TAG_LABELS.get(tag) ?? tag;
}

export function toggleNoteTag(tags: readonly ScoutNoteTag[], tag: ScoutNoteTag): ScoutNoteTag[] {
  return normalizeNoteTags(tags.includes(tag) ? tags.filter((value) => value !== tag) : [...tags, tag]);
}

export function filterTeamNotes(notes: readonly TeamScoutNote[], tag: string | null, query: string): TeamScoutNote[] {
  const needle = query.trim().toLowerCase();
  return notes.filter((note) => {
    if (tag && !note.tags.includes(tag)) return false;
    if (!needle) return true;
    return note.notes.toLowerCase().includes(needle) || note.scout_profile.toLowerCase().includes(needle);
  });
}
