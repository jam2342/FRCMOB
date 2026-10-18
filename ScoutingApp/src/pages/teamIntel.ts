import type { EventTeamRatingItem, TeamBreakdownResponse } from '../api';
import { asRecord, parseNumber, teamNumberFromTeamKey } from './centerUtils';

export type TeamIntelBreakdown = Omit<TeamBreakdownResponse, 'climb_sources' | 'matches_analyzed'> & {
  climb_sources?: Pick<NonNullable<TeamBreakdownResponse['climb_sources']>, 'level_capability'>;
};

import { parseOfficialStats } from './teamCenterOfficial';

export function breakdownFromIntel(
  intel: Record<string, unknown>,
  teamKey: string,
  includeDetails = false,
): TeamIntelBreakdown | null {
  const analysis = asRecord(intel.analysis);
  const team = asRecord(intel.team);
  if (!analysis || !team) return null;
  const averages = asRecord(analysis.averages);
  const climbSourcesRaw = includeDetails ? asRecord(analysis.climb_sources) : null;
  const climbLevelRaw = asRecord(climbSourcesRaw?.level_capability);
  const climbSources: TeamIntelBreakdown['climb_sources'] =
    includeDetails && climbSourcesRaw
      ? {
          level_capability: climbLevelRaw
            ? {
                best_level:
                  climbLevelRaw.best_level === 'level1' ||
                  climbLevelRaw.best_level === 'level2' ||
                  climbLevelRaw.best_level === 'level3'
                    ? climbLevelRaw.best_level
                    : null,
                best_level_label:
                  typeof climbLevelRaw.best_level_label === 'string' ? climbLevelRaw.best_level_label : 'No Level Data',
                best_level_score_0_100: parseNumber(climbLevelRaw.best_level_score_0_100),
                level_counts: {
                  level1: Math.max(0, Math.floor(parseNumber(asRecord(climbLevelRaw.level_counts)?.level1) ?? 0)),
                  level2: Math.max(0, Math.floor(parseNumber(asRecord(climbLevelRaw.level_counts)?.level2) ?? 0)),
                  level3: Math.max(0, Math.floor(parseNumber(asRecord(climbLevelRaw.level_counts)?.level3) ?? 0)),
                },
                matches_with_level: Math.max(0, Math.floor(parseNumber(climbLevelRaw.matches_with_level) ?? 0)),
                matches_with_success_no_level: Math.max(
                  0,
                  Math.floor(parseNumber(climbLevelRaw.matches_with_success_no_level) ?? 0),
                ),
                matches_considered: Math.max(0, Math.floor(parseNumber(climbLevelRaw.matches_considered) ?? 0)),
              }
            : undefined,
        }
      : undefined;
  return {
    ok: true,
    team: {
      team_key: String(team.team_key || teamKey).toLowerCase(),
      team_number: parseNumber(team.team_number) ?? teamNumberFromTeamKey(teamKey) ?? 0,
      nickname: typeof team.nickname === 'string' ? team.nickname : null,
    },
    event_key: typeof intel.event_key === 'string' ? intel.event_key : null,
    season_scope: asRecord(analysis.season_scope) as TeamBreakdownResponse['season_scope'],
    averages: averages
      ? {
          fuel_scoring_rate: parseNumber(averages.fuel_scoring_rate),
          cycle_time_sec: parseNumber(averages.cycle_time_sec),
          auto_contribution: parseNumber(averages.auto_contribution),
          climb_success_prob: parseNumber(averages.climb_success_prob),
          defensive_engagement_sec: parseNumber(averages.defensive_engagement_sec),
          reliability_score: parseNumber(averages.reliability_score),
        }
      : null,
    ...(includeDetails
      ? {
          climb_sources: climbSources,
          official_stats: parseOfficialStats(analysis.official_stats),
        }
      : {}),
    metric_coverage: (asRecord(analysis.metric_coverage) || {}) as TeamBreakdownResponse['metric_coverage'],
    event_type_counts: Array.isArray(analysis.event_type_counts)
      ? (analysis.event_type_counts as TeamBreakdownResponse['event_type_counts'])
      : [],
    recent_matches: Array.isArray(analysis.recent_matches)
      ? (analysis.recent_matches as TeamBreakdownResponse['recent_matches'])
      : [],
    recent_track_points: [],
  };
}

function ratingRange(value: unknown): [number, number] | null {
  if (!Array.isArray(value) || value.length !== 2) return null;
  const low = parseNumber(value[0]);
  const high = parseNumber(value[1]);
  return low !== null && high !== null ? [low, high] : null;
}

export function ratingFromIntel(
  intel: Record<string, unknown>,
  teamKey: string,
  teamNumber: number | null,
  nickname: string | null,
  includeDetails = false,
): EventTeamRatingItem | null {
  const rating = asRecord(intel.rating);
  if (!rating || !rating.available) return null;
  const subscores = asRecord(rating.subscores) || {};
  // Shown exactly as the server rated it. This used to re-blend the rating with an
  // "EPA" in the browser, so Team Center showed a different number for a team than
  // every other page.
  const displayOverall = parseNumber(rating.rating_0_100) ?? 50;
  const displayRobot = parseNumber(rating.robot_level_0_100) ?? 50;
  const displayDriver = parseNumber(rating.driver_skill_0_100);
  const points = asRecord(rating.expected_points);
  const total = parseNumber(points?.total);

  return {
    event_key:
      typeof rating.context_event_key === 'string' ? rating.context_event_key : (intel.event_key as string) || '',
    team_key: teamKey,
    team_number: teamNumber,
    nickname,
    rating_0_100: includeDetails ? Number(displayOverall.toFixed(1)) : displayOverall,
    confidence_0_1: parseNumber(rating.confidence_0_1) ?? 0,
    robot_level_0_100: includeDetails ? Number(displayRobot.toFixed(1)) : displayRobot,
    driver_skill_0_100: displayDriver === null || !includeDetails ? displayDriver : Number(displayDriver.toFixed(1)),
    // A missing subscore stays missing: it used to read as an average 50.
    subscores: {
      results_anchor: parseNumber(subscores.results_anchor),
      throughput: parseNumber(subscores.throughput),
      shift_productivity: parseNumber(subscores.shift_productivity),
      capacity_utilization: parseNumber(subscores.capacity_utilization),
      endgame: parseNumber(subscores.endgame),
      auto_contribution: parseNumber(subscores.auto_contribution),
      manual_points_impact: parseNumber(subscores.manual_points_impact),
      rp_contribution: parseNumber(subscores.rp_contribution),
      defense_presence: parseNumber(subscores.defense_presence),
      consistency: parseNumber(subscores.consistency),
      penalty_discipline: parseNumber(subscores.penalty_discipline),
    },
    expected_points:
      points && total !== null
        ? {
            total,
            teleop: parseNumber(points.teleop) ?? 0,
            auto: parseNumber(points.auto) ?? 0,
            climb: parseNumber(points.climb) ?? 0,
          }
        : null,
    basis:
      rating.basis === 'this_event' || rating.basis === 'earlier_events' || rating.basis === 'last_season'
        || rating.basis === 'rookie' || rating.basis === 'season_typical'
        ? rating.basis
        : null,
    matches_here: parseNumber(rating.matches_here),
    matches_before: parseNumber(rating.matches_before),
    rating_range_80: ratingRange(rating.rating_range_80),
    dud_rate: parseNumber(rating.dud_rate),
    recent_changes: Array.isArray(rating.recent_changes)
      ? (rating.recent_changes as unknown[]).flatMap((entry) => {
          const row = asRecord(entry);
          const points = parseNumber(row?.points_change);
          const change = parseNumber(row?.rating_change);
          return row && typeof row.match_key === 'string' && points !== null && change !== null
            ? [{ match_key: row.match_key, points_change: points, rating_change: change }]
            : [];
        })
      : [],
    ...(includeDetails
      ? {
          rank: parseNumber(rating.rank),
          field_size: parseNumber(rating.field_size),
          rank_event_key: typeof rating.rank_event_key === 'string' ? rating.rank_event_key : null,
        }
      : {}),
    pros: Array.isArray(rating.pros) ? (rating.pros as EventTeamRatingItem['pros']) : [],
    cons: Array.isArray(rating.cons) ? (rating.cons as EventTeamRatingItem['cons']) : [],
    evidence: Array.isArray(rating.evidence) ? (rating.evidence as EventTeamRatingItem['evidence']) : [],
    details: asRecord(rating.details) || {},
    model_version: typeof rating.model_version === 'string' ? rating.model_version : 'rating_v5_configured',
    updated_at: typeof rating.updated_at === 'string' ? rating.updated_at : null,
  };
}

/* A team with no rating this season: last season's, kept apart (a different game). */
export type PreviousSeasonRating = {
  season: number;
  game: string | null;
  eventKey: string;
  eventName: string | null;
  rating: number;
  rank: number | null;
  fieldSize: number | null;
};

export function previousSeasonFromIntel(intel: Record<string, unknown>): PreviousSeasonRating | null {
  const rating = asRecord(intel.rating);
  if (!rating || rating.available) return null;
  const previous = asRecord(rating.previous_season);
  const season = parseNumber(previous?.season);
  const value = parseNumber(previous?.rating_0_100);
  if (!previous || season === null || value === null || typeof previous.event_key !== 'string') return null;
  return {
    season,
    game: typeof previous.game === 'string' ? previous.game : null,
    eventKey: previous.event_key,
    eventName: typeof previous.event_name === 'string' ? previous.event_name : null,
    rating: value,
    rank: parseNumber(previous.rank),
    fieldSize: parseNumber(previous.field_size),
  };
}

// "Last season (2026 · REBUILT): 83.2, 1st of 42 at Lake Superior Regional".
export function previousSeasonText(previous: PreviousSeasonRating): string {
  const label = previous.game ? `${previous.season} · ${previous.game}` : String(previous.season);
  const where = previous.eventName || previous.eventKey;
  const place = previous.rank && previous.fieldSize ? `, ${ordinalNumber(previous.rank)} of ${previous.fieldSize} at ${where}` : ` at ${where}`;
  return `Last season (${label}): ${previous.rating.toFixed(1)}${place}. A different game, so it doesn't compare with this season.`;
}

function ordinalNumber(n: number): string {
  const tens = n % 100;
  const suffix = tens >= 11 && tens <= 13 ? 'th' : ({ 1: 'st', 2: 'nd', 3: 'rd' } as Record<number, string>)[n % 10] || 'th';
  return `${n}${suffix}`;
}
