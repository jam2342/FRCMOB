import type { EventTeamRatingItem, TeamBreakdownResponse } from '../api';
import { asRecord, parseNumber, teamNumberFromTeamKey } from './centerUtils';

export type TeamIntelBreakdown = Omit<TeamBreakdownResponse, 'climb_sources'> & {
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
    data_freshness: asRecord(analysis.data_freshness) as TeamBreakdownResponse['data_freshness'],
    matches_analyzed: parseNumber(analysis.matches_analyzed) ?? 0,
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
          metric_units: (asRecord(analysis.metric_units) || {}) as Record<string, string>,
          climb_sources: climbSources,
          official_stats: parseOfficialStats(analysis.official_stats),
        }
      : {}),
    metric_coverage: (asRecord(analysis.metric_coverage) || {}) as TeamBreakdownResponse['metric_coverage'],
    active_perimeter_type:
      analysis.active_perimeter_type === 'welded' || analysis.active_perimeter_type === 'andymark'
        ? analysis.active_perimeter_type
        : null,
    perimeter_types: Array.isArray(analysis.perimeter_types)
      ? analysis.perimeter_types.filter(
          (value): value is 'welded' | 'andymark' => value === 'welded' || value === 'andymark',
        )
      : [],
    perimeter_sources: Array.isArray(analysis.perimeter_sources)
      ? analysis.perimeter_sources.map((value) => String(value))
      : [],
    analysis_versions: Array.isArray(analysis.analysis_versions)
      ? analysis.analysis_versions.map((value) => String(value))
      : [],
    event_type_counts: Array.isArray(analysis.event_type_counts)
      ? (analysis.event_type_counts as TeamBreakdownResponse['event_type_counts'])
      : [],
    zone_time_sec: (asRecord(analysis.zone_time_sec) || {}) as Record<string, number>,
    recent_matches: Array.isArray(analysis.recent_matches)
      ? (analysis.recent_matches as TeamBreakdownResponse['recent_matches'])
      : [],
    recent_events: [],
    recent_track_points: [],
    run_ids: Array.isArray(analysis.run_ids)
      ? analysis.run_ids.map((value) => parseNumber(value)).filter((value): value is number => value !== null)
      : [],
  };
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
  const displayDriver = parseNumber(rating.driver_skill_0_100) ?? 50;

  return {
    event_key:
      typeof rating.context_event_key === 'string' ? rating.context_event_key : (intel.event_key as string) || '',
    team_key: teamKey,
    team_number: teamNumber,
    nickname,
    rating_0_100: includeDetails ? Number(displayOverall.toFixed(1)) : displayOverall,
    confidence_0_1: parseNumber(rating.confidence_0_1) ?? 0,
    robot_level_0_100: includeDetails ? Number(displayRobot.toFixed(1)) : displayRobot,
    driver_skill_0_100: includeDetails ? Number(displayDriver.toFixed(1)) : displayDriver,
    subscores: {
      results_anchor: parseNumber(subscores.results_anchor) ?? 50,
      throughput: parseNumber(subscores.throughput) ?? 50,
      shift_productivity: parseNumber(subscores.shift_productivity) ?? 50,
      capacity_utilization: parseNumber(subscores.capacity_utilization) ?? 50,
      endgame: parseNumber(subscores.endgame) ?? 50,
      auto_contribution: parseNumber(subscores.auto_contribution),
      manual_points_impact: parseNumber(subscores.manual_points_impact),
      rp_contribution: parseNumber(subscores.rp_contribution),
      defense_presence: parseNumber(subscores.defense_presence),
      consistency: parseNumber(subscores.consistency) ?? 50,
      penalty_discipline: parseNumber(subscores.penalty_discipline),
    },
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
