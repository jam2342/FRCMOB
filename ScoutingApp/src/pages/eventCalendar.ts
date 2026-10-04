// Event date and calendar helpers shared by Home and Events (they were two copies).
import type { EventScheduleItem, EventSearchItem } from '../api';

export type EventDateRange = {
  startMs: number | null;
  endMs: number | null;
};

export function parseEventDateValue(value: string | null | undefined): number | null {
  if (!value || typeof value !== 'string') return null;
  const token = value.trim().slice(0, 10);
  if (!token) return null;
  const ms = Date.parse(`${token}T00:00:00Z`);
  return Number.isFinite(ms) ? ms : null;
}

export function parseEventYearValue(value: unknown): number | null {
  const normalized = typeof value === 'number' ? value : Number(String(value || '').trim());
  if (!Number.isFinite(normalized)) return null;
  const year = Math.trunc(normalized);
  if (year < 1992 || year > 2100) return null;
  return year;
}

export function yearFromEventKey(eventKey: string): number | null {
  const match = /^(\d{4})/.exec((eventKey || '').trim().toLowerCase());
  if (!match) return null;
  return parseEventYearValue(match[1]);
}

export function normalizeDateRange(range: EventDateRange): EventDateRange {
  if (range.startMs && range.endMs && range.endMs < range.startMs) {
    return { startMs: range.endMs, endMs: range.startMs };
  }
  return range;
}

export function resolveEventDateRange(event: EventSearchItem, fallback?: EventDateRange): EventDateRange {
  const startMs = parseEventDateValue(event.start_date ?? null) ?? fallback?.startMs ?? null;
  const endMs = parseEventDateValue(event.end_date ?? null) ?? fallback?.endMs ?? startMs;
  return normalizeDateRange({ startMs, endMs });
}

export function resolveCalendarYear(event: EventSearchItem, fallback?: EventDateRange): number | null {
  const explicit = parseEventYearValue(event.year);
  if (explicit !== null) return explicit;
  const resolved = resolveEventDateRange(event, fallback);
  const ms = resolved.startMs ?? resolved.endMs;
  if (ms) return new Date(ms).getUTCFullYear();
  return yearFromEventKey(event.event_key || '');
}

export function matchesCalendarDisplayYear(
  event: EventSearchItem,
  displayYear: number,
  fallback?: EventDateRange,
): boolean {
  const keyYear = yearFromEventKey(event.event_key || '');
  if (keyYear !== null && keyYear !== displayYear) return false;
  return resolveCalendarYear(event, fallback) === displayYear;
}

export function monthTokenFromMs(ms: number): string {
  const d = new Date(ms);
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}`;
}

export function shiftMonthToken(monthToken: string, delta: number): string {
  const token = (monthToken || '').trim();
  const match = /^(\d{4})-(\d{2})$/.exec(token);
  const base = match
    ? new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, 1))
    : new Date(Date.UTC(new Date().getUTCFullYear(), new Date().getUTCMonth(), 1));
  base.setUTCMonth(base.getUTCMonth() + delta);
  const year = base.getUTCFullYear();
  const month = String(base.getUTCMonth() + 1).padStart(2, '0');
  return `${year}-${month}`;
}

export function compactAllianceLabel(teams: EventScheduleItem['red']): string {
  if (!Array.isArray(teams) || teams.length === 0) return 'TBD';
  const labels = teams.map((team) => `#${team.team_number}`);
  if (labels.length <= 2) return labels.join(' · ');
  return `${labels.slice(0, 2).join(' · ')} +${labels.length - 2}`;
}
