import type { PitScoutingEntry } from '../../api';
import { PIT_FORM_SECTIONS, type PitFieldDef } from '../../config/gameFields';

// What a decision needs first: drivetrain, climb, claimed auto, cycle time.
export const PIT_KEY_FIELDS = ['drivetrain', 'claimed_climb', 'auto_capabilities', 'claimed_cycle_time_sec'] as const;

// Compare adds the preferred role: it decides who plays which side of the alliance.
export const PIT_COMPARE_FIELDS = [...PIT_KEY_FIELDS, 'preferred_role'] as const;

export interface PitAnswer {
  field: PitFieldDef;
  text: string;
}

export interface PitAnswerSection {
  title: string;
  answers: PitAnswer[];
}

export function formatPitAnswer(field: PitFieldDef, value: unknown): string | null {
  if (value === null || value === undefined) return null;
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  if (Array.isArray(value)) {
    const items = value.map((item) => String(item).trim()).filter(Boolean);
    return items.length ? items.join(', ') : null;
  }
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) return null;
    return field.unit ? `${value} ${field.unit}` : String(value);
  }
  const text = String(value).trim();
  if (!text) return null;
  // A number that arrived as a string still gets its unit.
  return field.type === 'number' && field.unit && /^-?\d+(\.\d+)?$/.test(text) ? `${text} ${field.unit}` : text;
}

export function pitAnswerFor(entry: PitScoutingEntry | null | undefined, fieldKey: string): PitAnswer | null {
  if (!entry) return null;
  for (const section of PIT_FORM_SECTIONS) {
    const field = section.fields.find((candidate) => candidate.key === fieldKey);
    if (!field) continue;
    const text = formatPitAnswer(field, entry.payload?.[field.key]);
    return text === null ? null : { field, text };
  }
  return null;
}

export function pitAnswerSections(entry: PitScoutingEntry): PitAnswerSection[] {
  return PIT_FORM_SECTIONS.map((section) => ({
    title: section.title,
    answers: section.fields.flatMap((field) => {
      const text = formatPitAnswer(field, entry.payload?.[field.key]);
      return text === null ? [] : [{ field, text }];
    }),
  })).filter((section) => section.answers.length > 0);
}

export function pitFieldLabel(fieldKey: string): string {
  for (const section of PIT_FORM_SECTIONS) {
    const field = section.fields.find((candidate) => candidate.key === fieldKey);
    if (field) return field.label;
  }
  return fieldKey;
}

export function pitPhotoCount(entry: PitScoutingEntry): number {
  return entry.photos?.length || entry.photo_urls?.length || 0;
}

export function pitTeamCenterPath(teamKey: string, eventKey: string): string {
  const params = new URLSearchParams({ team: teamKey });
  if (eventKey) params.set('event', eventKey);
  params.set('tab', 'media');
  return `/team-center?${params.toString()}`;
}
