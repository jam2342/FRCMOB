import { PIT_FORM_SECTIONS } from '../../config/gameFields';

// A pit save sends what this scout changed since opening the team ({field: {from, to}}),
// and the server merges it into what teammates saved. A missing "from"/"to" means the
// field had no answer. The "base" is the server's answers when this scout started.

export type Answers = Record<string, unknown>;
export type PitChange = { from?: unknown; to?: unknown };
export type PitChanges = Record<string, PitChange>;

const blank = (value: unknown) => value === undefined || value === null;

// "No answer" is one thing whether the key is missing or null; lists compare by content.
export function sameAnswer(a: unknown, b: unknown): boolean {
  if (blank(a) || blank(b)) return blank(a) && blank(b);
  if (typeof a === 'object' || typeof b === 'object') return JSON.stringify(a) === JSON.stringify(b);
  return a === b;
}

// `force` lists fields to send even when unchanged: picking an answer in a conflict.
export function buildChanges(base: Answers, form: Answers, force: Answers = {}): PitChanges {
  const changes: PitChanges = {};
  for (const key of new Set([...Object.keys(base), ...Object.keys(form)])) {
    if (sameAnswer(base[key], form[key])) continue;
    changes[key] = {
      ...(blank(base[key]) ? {} : { from: base[key] }),
      ...(form[key] === undefined ? {} : { to: form[key] }),
    };
  }
  for (const [key, from] of Object.entries(force)) {
    changes[key] = { ...(blank(from) ? {} : { from }), ...(form[key] === undefined ? {} : { to: form[key] }) };
  }
  return changes;
}

// The fields this scout has changed since the base.
export function touchedFields(base: Answers, form: Answers): string[] {
  return [...new Set([...Object.keys(base), ...Object.keys(form)])].filter((key) => !sameAnswer(base[key], form[key]));
}

// A teammate's save arrives while this scout has edits open: take their answers for
// every field this scout hasn't touched, keep this scout's for the rest. `clashes` are
// fields both changed differently; the server keeps both when this scout saves.
export function mergeTeammate(base: Answers, form: Answers, server: Answers): { base: Answers; form: Answers; clashes: string[] } {
  const touched = touchedFields(base, form);
  const nextBase: Answers = { ...server };
  const nextForm: Answers = { ...server };
  for (const key of touched) {
    if (key in base) nextBase[key] = base[key];
    else delete nextBase[key];
    if (form[key] === undefined) delete nextForm[key];
    else nextForm[key] = form[key];
  }
  const clashes = touched.filter((key) => !sameAnswer(server[key], base[key]) && !sameAnswer(server[key], form[key]));
  return { base: nextBase, form: nextForm, clashes };
}

const FIELDS = new Map(PIT_FORM_SECTIONS.flatMap((section) => section.fields).map((field) => [field.key, field]));

export function fieldLabel(key: string): string {
  return FIELDS.get(key)?.label ?? key;
}

// How an answer reads in a sentence: "Swerve", "Yes", "112 lb", "Ground, Source".
export function formatAnswer(key: string, value: unknown): string {
  if (blank(value) || value === '') return 'no answer';
  if (value === true) return 'Yes';
  if (value === false) return 'No';
  if (Array.isArray(value)) return value.length ? value.join(', ') : 'no answer';
  const unit = FIELDS.get(key)?.unit;
  return typeof value === 'number' && unit ? `${value} ${unit}` : String(value);
}
