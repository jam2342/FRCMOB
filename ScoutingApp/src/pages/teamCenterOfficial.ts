import type { OfficialTeamStats } from '../api';

function asNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

export function parseOfficialStats(raw: unknown): OfficialTeamStats | null {
  if (!raw || typeof raw !== 'object') return null;
  const record = raw as Record<string, unknown>;
  if (record.available !== true) return null;
  const climb = record.climb && typeof record.climb === 'object' ? (record.climb as Record<string, unknown>) : null;
  return {
    available: true,
    matches: asNumber(record.matches) ?? 0,
    copr_matches: asNumber(record.copr_matches) ?? 0,
    fuel_per_match: asNumber(record.fuel_per_match),
    fuel_per_active_minute: asNumber(record.fuel_per_active_minute),
    auto_points_per_match: asNumber(record.auto_points_per_match),
    events: Array.isArray(record.events)
      ? record.events
          .filter((event): event is Record<string, unknown> => Boolean(event) && typeof event === 'object')
          .map((event) => ({
            event_key: String(event.event_key ?? ''),
            matches: asNumber(event.matches) ?? 0,
            copr: event.copr === true,
          }))
      : [],
    climb: climb
      ? { matches: asNumber(climb.matches) ?? 0, climbs: asNumber(climb.climbs) ?? 0, rate: asNumber(climb.rate) }
      : null,
  };
}

// The one line under "FRC Scouting Metrics" that says where the numbers came
// from when nobody has scouted the team.
export function officialMetricsNote(stats: OfficialTeamStats | null | undefined): string {
  if (!stats?.available) {
    return 'Nobody has scouted this team yet, and it has no official results this season.';
  }
  const eventCount = stats.events.filter((event) => event.copr).length;
  if (stats.copr_matches > 0) {
    return (
      `Nobody has scouted this team yet. Fuel and auto are TBA's per-team estimates from ` +
      `${stats.copr_matches} official matches at ${eventCount} event${eventCount === 1 ? '' : 's'} this season, ` +
      `so one event's numbers can differ; climb is its official record.`
    );
  }
  return "Nobody has scouted this team yet. Climb is its official record; TBA's per-team fuel estimates aren't available.";
}

export function scoreAgainst(value: number | null | undefined, elite: number): number | null {
  if (typeof value !== 'number' || !Number.isFinite(value) || elite <= 0) return null;
  return Math.max(0, Math.min(100, (value / elite) * 100));
}

// "0.00 / 99.8%" was the raw metric and its percentile side by side, which
// nobody outside the ratings code can read. Where the team sits in the field
// is the part that means something.
export function signalRankLabel(percentile: number | null | undefined, kind: 'strength' | 'risk'): string {
  if (typeof percentile !== 'number' || !Number.isFinite(percentile)) return '';
  const clamped = Math.min(100, Math.max(0, percentile));
  if (kind === 'strength') return `Top ${Math.max(1, Math.round(100 - clamped))}%`;
  return `Bottom ${Math.max(1, Math.round(clamped))}%`;
}

// Broadcast video was retired (2026-09-28); a "no analyzed clips" risk is
// about a pipeline that no longer exists, not about the robot.
export function isRobotSignal(label: string): boolean {
  return !/\b(clips?|video|footage)\b/i.test(label || '');
}
