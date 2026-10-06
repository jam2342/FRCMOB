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

// The ratings engine names its signals for analysts ("RP threshold contributor", "External EPA
// baseline is low"). They're stored with each rating, so they're translated where they're shown.
const PLAIN_SIGNAL_LABELS: Record<string, string> = {
  'High external EPA baseline': 'Strong Statbotics rating',
  'External EPA baseline is low': 'Weak Statbotics rating',
  'Strong EPA baseline': 'Strong Statbotics rating',
  'Low EPA baseline': 'Weak Statbotics rating',
  'Strong OPR projection': 'Scores a lot in official results',
  'Weak win-margin projection': 'Expected to win by little, or lose',
  'ML throughput projection is strong': 'Expected to score a lot',
  'ML throughput projection is below field': 'Expected to score below average',
  'Throughput elite': 'Elite scorer',
  'Shift dominance': 'Scores heavily while its hub is active',
  'Tower threat': 'Climbs well',
  'Strong autonomous impact': 'Strong autonomous',
  'Weak autonomous output': 'Weak autonomous',
  'RP threshold contributor': 'Helps earn bonus ranking points',
  'Low RP threshold contribution': 'Rarely helps earn bonus ranking points',
  'High net point impact': 'Adds a lot of points to its alliance',
  'Low net point impact': 'Adds few points to its alliance',
  'High driver carry': 'Its drivers lift its scoring',
  'High ceiling': 'Has big best-case matches',
  'Pressure-resistant scoring': 'Keeps scoring under defense',
  'Drops under defensive pressure': 'Struggles against defense',
  'Output trend accelerating': 'Scoring is trending up',
  'Output trend cooling': 'Scoring is trending down',
  'Reliability trend improving': 'Getting more reliable',
  'Reliability trend declining': 'Getting less reliable',
  'Underutilizes capacity': 'Could score more than it does',
  'Execution limiting output': 'Mistakes are costing it points',
  'Low shooting throughput': 'Scores little fuel',
  'Endgame missing': 'Rarely climbs',
  'Inconsistent output': 'Up and down from match to match',
  'Penalty risk impacts expected points': 'Penalties cost it points',
  'Penalty pressure worsening': 'Drawing more penalties lately',
  'Cycle timing regressing': 'Cycles are getting slower',
  'Disciplined play': 'Few penalties',
  'Limited current evidence': 'Few matches to judge from',
  'Autonomous floor is serviceable': 'Reliable autonomous',
  'Autonomous floor needs work': 'Autonomous needs work',
  'Teleop baseline is stable': 'Steady teleop scoring',
  'Teleop baseline is limited': 'Limited teleop scoring',
  'Endgame baseline is viable': 'Can climb',
  'Endgame baseline is limited': 'Rarely climbs',
  'Defense utility baseline': 'Useful on defense',
  'Defense utility is limited': 'Not much use on defense',
  'Anti-defense baseline': 'Handles defense well',
  'Anti-defense risk profile': 'Struggles against defense',
  'Clean-play baseline': 'Plays clean',
  'Discipline risk baseline': 'Some penalty risk',
};

export function plainSignalLabel(label: string): string {
  return PLAIN_SIGNAL_LABELS[(label || '').trim()] ?? label;
}
