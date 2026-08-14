import { type FormEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import {
  getEventSchedule,
  getEventTeamLiveForm,
  getEventTeamsIntel,
  getTeamIntel,
} from '../api';
import type { EventScheduleItem, TeamCompetitionsResponse } from '../api';
import { SegmentedTabs } from '../components/ui/SegmentedTabs';
import { SurfaceCard, SurfaceCardGroup } from '../components/ui/SurfaceCard';
import { useLiveRefreshSetting } from '../hooks/useLiveRefreshSetting';
import { useMobileLayout } from '../hooks/useMobileLayout';
import { usePageClock } from '../hooks/usePageClock';
import { usePageVisibility } from '../hooks/usePageVisibility';
import { useSingleFlightPolling } from '../hooks/useSingleFlightPolling';
import {
  asRecord,
  buildMatchCenterPath,
  CURRENT_SEASON_YEAR,
  fmtDateShort,
  liveTimerLabel,
  metric,
  metricUnit,
  normalizeTeamKeyInput,
  parseNumber,
  pct,
  relativeFromTimestamp,
  summarizeFreshness,
  teamNumberFromTeamKey,
  titleizeKey,
} from './centerUtils';
import {
  readFavoriteEvents,
  readFavoriteTeams,
  saveFavoriteEvents,
  saveFavoriteTeams,
} from '../layout/userSettings';
import { readStoredCenterContext } from '../layout/centerContext';

type FavoritesTab = 'overview' | 'events' | 'teams';

type FavoriteEventCard = {
  event_key: string;
  event_name: string;
  schedule_count: number;
  teams_count: number;
  completed_count: number;
  live_team_count: number;
  live_matches: EventScheduleItem[];
  upcoming_matches: EventScheduleItem[];
  warnings: string[];
  last_updated_at: number;
};

type FavoriteTeamCard = {
  team_key: string;
  team_number: number | null;
  nickname: string;
  matches_analyzed: number;
  fuel_rate: number | null;
  cycle_time_sec: number | null;
  auto_contribution: number | null;
  climb_success_prob: number | null;
  reliability_score: number | null;
  rating_0_100: number | null;
  confidence_0_1: number | null;
  rating_robot_level_0_100: number | null;
  rating_driver_skill_0_100: number | null;
  tba_rank: number | null;
  tba_record: string;
  tba_event_awards_count: number | null;
  tba_year_awards_count: number | null;
  freshness_state: 'fresh' | 'stale' | 'unknown';
  freshness_label: string;
  freshness_detail: string | null;
  events_count: number;
  registered_events: TeamCompetitionsResponse['registered_events'];
  next_event_key: string | null;
  warnings: string[];
  error: string;
  last_updated_at: number;
};

type LiveFavoriteMatch = {
  event_key: string;
  event_name: string;
  match: EventScheduleItem;
};

function isEventKey(value: string): boolean {
  return /^\d{4}[a-z0-9]+$/.test(value);
}

function normalizeEventKey(value: string): string {
  return value.trim().toLowerCase();
}

function isFavoritesTab(value: string | null): value is FavoritesTab {
  return value === 'overview' || value === 'events' || value === 'teams';
}

function eventDisplayName(card: FavoriteEventCard): string {
  return card.event_name && card.event_name !== card.event_key ? card.event_name : card.event_key.toUpperCase();
}

export function FavoritesPage() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const isMobileLayout = useMobileLayout();
  const pageVisible = usePageVisibility();
  const nowMs = usePageClock(pageVisible);
  const liveRefreshSec = useLiveRefreshSetting();
  const tabParam = searchParams.get('tab');
  const defaultTab: FavoritesTab = isFavoritesTab(tabParam) ? tabParam : 'overview';

  const [activeTab, setActiveTab] = useState<FavoritesTab>(defaultTab);
  const [favoriteEvents, setFavoriteEvents] = useState<string[]>(() => readFavoriteEvents());
  const [favoriteTeams, setFavoriteTeams] = useState<string[]>(() => readFavoriteTeams());

  const [eventInput, setEventInput] = useState('');
  const [teamInput, setTeamInput] = useState('');
  const [eventContextInput, setEventContextInput] = useState(() =>
    readStoredCenterContext().eventKey,
  );

  const [eventCards, setEventCards] = useState<Record<string, FavoriteEventCard>>({});
  const [teamCards, setTeamCards] = useState<Record<string, FavoriteTeamCard>>({});

  const [loadingEvents, setLoadingEvents] = useState(false);
  const [loadingTeams, setLoadingTeams] = useState(false);
  const [statusText, setStatusText] = useState('Add teams and events to track.');
  const [errorText, setErrorText] = useState('');
  const [lastUpdatedAt, setLastUpdatedAt] = useState<number | null>(null);
  const [visibleEventCardCount, setVisibleEventCardCount] = useState(20);
  const [visibleTeamCardCount, setVisibleTeamCardCount] = useState(20);
  const [mobileFinderOpen, setMobileFinderOpen] = useState(false);
  const refreshInFlightRef = useRef(false);

  useEffect(() => {
    if (!isMobileLayout) setMobileFinderOpen(false);
  }, [isMobileLayout]);

  // Sync activeTab → searchParams (one-way: activeTab is source of truth).
  // Reading searchParams happens only on mount via the defaultTab initial state.
  useEffect(() => {
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      if (activeTab === 'overview') {
        next.delete('tab');
      } else {
        next.set('tab', activeTab);
      }
      return next.toString() === current.toString() ? current : next;
    }, { replace: true });
  }, [activeTab, setSearchParams]);

  useEffect(() => {
    setVisibleEventCardCount(20);
  }, [favoriteEvents.length]);

  useEffect(() => {
    setVisibleTeamCardCount(20);
  }, [favoriteTeams.length]);

  const eventContextKey = useMemo(() => normalizeEventKey(eventContextInput), [eventContextInput]);

  function persistFavoriteEvents(next: string[]) {
    const normalized = saveFavoriteEvents(next);
    setFavoriteEvents(normalized);
  }

  function persistFavoriteTeams(next: string[]) {
    const normalized = saveFavoriteTeams(next);
    setFavoriteTeams(normalized);
  }

  const refreshFavorites = useCallback(async (options?: { forceNetwork?: boolean }) => {
    if (refreshInFlightRef.current) return;
    refreshInFlightRef.current = true;
    const forceNetwork = Boolean(options?.forceNetwork);
    setErrorText('');
    try {
      if (favoriteEvents.length === 0 && favoriteTeams.length === 0) {
        setEventCards({});
        setTeamCards({});
        setStatusText('No favorites yet.');
        setLastUpdatedAt(Date.now());
        return;
      }

      setLoadingEvents(true);
      setLoadingTeams(true);
      setStatusText('Refreshing...');

      const eventResults = await Promise.allSettled(
        favoriteEvents.map(async (eventKey) => {
          const warnings: string[] = [];
          const [scheduleResult, teamsResult, liveFormResult] = await Promise.allSettled([
            getEventSchedule(eventKey, forceNetwork, undefined, { bypassCache: forceNetwork }),
            getEventTeamsIntel(
              eventKey,
              {
                include_tba: true,
                include_statbotics: false,
                include_season_fallback: true,
                include_rating_details: false,
                include_rating_signals: false,
                auto_heal_ratings: true,
                refresh: forceNetwork,
              },
              { bypassCache: forceNetwork },
            ),
            getEventTeamLiveForm(eventKey, { form_window: 5, live_window_sec: 180 }, { bypassCache: forceNetwork }),
          ]);

        let scheduleMatches: EventScheduleItem[] = [];
        let eventName = eventKey;
        let teamsCount = 0;
        let liveTeamCount = 0;

        if (scheduleResult.status === 'fulfilled') {
          scheduleMatches = scheduleResult.value.matches || [];
          eventName = scheduleResult.value.event_name || eventName;
        } else {
          warnings.push(`Schedule unavailable: ${(scheduleResult.reason as Error).message || 'failed'}`);
        }

        if (teamsResult.status === 'fulfilled') {
          teamsCount = teamsResult.value.teams_count || (teamsResult.value.teams || []).length;
          if (!eventName || eventName === eventKey) {
            eventName = teamsResult.value.event_name || eventName;
          }
          for (const warning of teamsResult.value.warnings || []) {
            warnings.push(`Intel: ${warning}`);
          }
        } else {
          warnings.push(`Teams unavailable: ${(teamsResult.reason as Error).message || 'failed'}`);
        }

        if (liveFormResult.status === 'fulfilled') {
          const statuses = Object.values(liveFormResult.value.team_statuses || {});
          liveTeamCount = statuses.filter((item) => item?.is_live).length;
        } else {
          warnings.push(`Live form unavailable: ${(liveFormResult.reason as Error).message || 'failed'}`);
        }

        const liveMatches: EventScheduleItem[] = [];
        const upcomingMatches: EventScheduleItem[] = [];
        let completedCount = 0;

        for (const match of scheduleMatches) {
          const timer = liveTimerLabel(match.scheduled_time, Date.now());
          if (timer.state === 'live') liveMatches.push(match);
          if (timer.state === 'upcoming') upcomingMatches.push(match);
          if (timer.state === 'ended' || match.is_completed) completedCount += 1;
        }

        upcomingMatches.sort((a, b) => {
          const aTime = a.scheduled_time || Number.MAX_SAFE_INTEGER;
          const bTime = b.scheduled_time || Number.MAX_SAFE_INTEGER;
          if (aTime !== bTime) return aTime - bTime;
          return a.match_key.localeCompare(b.match_key);
        });

        const card: FavoriteEventCard = {
          event_key: eventKey,
          event_name: eventName,
          schedule_count: scheduleMatches.length,
          teams_count: teamsCount,
          completed_count: completedCount,
          live_team_count: liveTeamCount,
          live_matches: liveMatches,
          upcoming_matches: upcomingMatches,
          warnings,
          last_updated_at: Date.now(),
        };

          return card;
        }),
      );

      const teamResults = await Promise.allSettled(
        favoriteTeams.map(async (teamKey) => {
          const warnings: string[] = [];

        let error = '';
        let teamNumber = teamNumberFromTeamKey(teamKey);
        let nickname = teamKey;
        let matchesAnalyzed = 0;
        let fuelRate: number | null = null;
        let cycleTimeSec: number | null = null;
        let autoContribution: number | null = null;
        let climbSuccessProb: number | null = null;
        let reliabilityScore: number | null = null;
        let eventsCount = 0;
        let nextEventKey: string | null = null;
        let rating: number | null = null;
        let confidence: number | null = null;
        let ratingRobotLevel: number | null = null;
        let ratingDriverSkill: number | null = null;
        let tbaRank: number | null = null;
        let tbaRecord = 'N/A';
        let tbaEventAwardsCount: number | null = null;
        let tbaYearAwardsCount: number | null = null;
        let freshnessState: FavoriteTeamCard['freshness_state'] = 'unknown';
        let freshnessLabel = 'Freshness: N/A';
        let freshnessDetail: string | null = null;
        let registeredEvents: TeamCompetitionsResponse['registered_events'] = [];

          try {
            const intel = await getTeamIntel(
              teamKey,
              {
                event_key: eventContextKey || undefined,
                preferred_year: CURRENT_SEASON_YEAR,
                fallback_year: CURRENT_SEASON_YEAR - 1,
                include_tba: true,
                include_statbotics: false,
                allow_season_fallback: true,
                auto_heal_ratings: true,
                refresh: forceNetwork,
              },
              { bypassCache: forceNetwork },
            );

          const intelTeam = asRecord(intel.team);
          const analysis = asRecord(intel.analysis);
          const averages = asRecord(analysis?.averages);
          const competitions = asRecord(intel.competitions);
          const ratingPayload = asRecord(intel.rating);
          const tbaPayload = asRecord(intel.tba);
          const tbaSummary = asRecord(tbaPayload?.event_status_summary);
          const tbaAwards = Array.isArray(tbaPayload?.awards) ? tbaPayload.awards.map((item) => asRecord(item)).filter(Boolean) : [];

          teamNumber = parseNumber(intelTeam?.team_number) ?? teamNumber;
          nickname = typeof intelTeam?.nickname === 'string' ? intelTeam.nickname : nickname;
          matchesAnalyzed = parseNumber(analysis?.matches_analyzed) ?? 0;
          const freshness = summarizeFreshness(analysis?.data_freshness || null);
          freshnessState = freshness.state;
          freshnessLabel = freshness.label;
          freshnessDetail = freshness.detail;
          fuelRate = parseNumber(averages?.fuel_scoring_rate);
          cycleTimeSec = parseNumber(averages?.cycle_time_sec);
          autoContribution = parseNumber(averages?.auto_contribution);
          climbSuccessProb = parseNumber(averages?.climb_success_prob);
          reliabilityScore = parseNumber(averages?.reliability_score);

          eventsCount = parseNumber(competitions?.registered_events_count) ?? 0;
          registeredEvents = Array.isArray(competitions?.registered_events)
            ? (competitions.registered_events as TeamCompetitionsResponse['registered_events'])
            : [];
          const upcoming = [...registeredEvents].sort((a, b) => {
            const aDate = a.start_date || '';
            const bDate = b.start_date || '';
            return aDate.localeCompare(bDate);
          });
          nextEventKey = upcoming[0]?.event_key?.toLowerCase() || null;

          rating = parseNumber(ratingPayload?.rating_0_100);
          confidence = parseNumber(ratingPayload?.confidence_0_1);
          ratingRobotLevel = parseNumber(ratingPayload?.robot_level_0_100);
          ratingDriverSkill = parseNumber(ratingPayload?.driver_skill_0_100);

          tbaRank = parseNumber(tbaSummary?.rank);
          const tbaRecordPayload = asRecord(tbaSummary?.record);
          if (tbaRecordPayload) {
            const wins = parseNumber(tbaRecordPayload.wins) ?? 0;
            const losses = parseNumber(tbaRecordPayload.losses) ?? 0;
            const ties = parseNumber(tbaRecordPayload.ties) ?? 0;
            tbaRecord = `${wins}-${losses}-${ties}`;
          }
          tbaYearAwardsCount = tbaAwards.length;
          if (eventContextKey) {
            tbaEventAwardsCount = tbaAwards.filter(
              (award) => typeof award?.event_key === 'string' && award.event_key.toLowerCase() === eventContextKey,
            ).length;
          }

          for (const warning of Array.isArray(intel.warnings) ? intel.warnings : []) {
            warnings.push(`Intel: ${String(warning)}`);
          }
        } catch (requestError) {
          error = (requestError as Error).message || 'Intel unavailable.';
        }

          const card: FavoriteTeamCard = {
            team_key: teamKey,
            team_number: teamNumber,
            nickname,
            matches_analyzed: matchesAnalyzed,
            fuel_rate: fuelRate,
            cycle_time_sec: cycleTimeSec,
            auto_contribution: autoContribution,
            climb_success_prob: climbSuccessProb,
            reliability_score: reliabilityScore,
            rating_0_100: rating,
            confidence_0_1: confidence,
            rating_robot_level_0_100: ratingRobotLevel,
            rating_driver_skill_0_100: ratingDriverSkill,
            tba_rank: tbaRank,
            tba_record: tbaRecord,
            tba_event_awards_count: tbaEventAwardsCount,
            tba_year_awards_count: tbaYearAwardsCount,
            freshness_state: freshnessState,
            freshness_label: freshnessLabel,
            freshness_detail: freshnessDetail,
            events_count: eventsCount,
            registered_events: registeredEvents,
            next_event_key: nextEventKey,
            warnings,
            error,
            last_updated_at: Date.now(),
          };

          return card;
        }),
      );

      const nextEventCards: Record<string, FavoriteEventCard> = {};
      const nextTeamCards: Record<string, FavoriteTeamCard> = {};
      const errors: string[] = [];

      for (let index = 0; index < eventResults.length; index += 1) {
        const result = eventResults[index];
        const key = favoriteEvents[index];
        if (!key) continue;
        if (result.status === 'fulfilled') {
          nextEventCards[key] = result.value;
        } else {
          errors.push(`${key}: ${(result.reason as Error).message || 'event refresh failed'}`);
        }
      }

      for (let index = 0; index < teamResults.length; index += 1) {
        const result = teamResults[index];
        const key = favoriteTeams[index];
        if (!key) continue;
        if (result.status === 'fulfilled') {
          nextTeamCards[key] = result.value;
        } else {
          errors.push(`${key}: ${(result.reason as Error).message || 'team refresh failed'}`);
        }
      }

      setEventCards(nextEventCards);
      setTeamCards(nextTeamCards);
      setLastUpdatedAt(Date.now());

      if (errors.length > 0) {
        setErrorText(errors.join(' | '));
        setStatusText('Partial data loaded.');
      } else {
        setStatusText('Favorites refreshed.');
      }
    } catch (error) {
      setErrorText((error as Error).message || 'Favorites refresh failed.');
      setStatusText('Refresh failed.');
    } finally {
      setLoadingEvents(false);
      setLoadingTeams(false);
      refreshInFlightRef.current = false;
    }
  }, [eventContextKey, favoriteEvents, favoriteTeams]);

  const pollRunner = useCallback(() => refreshFavorites(), [refreshFavorites]);

  useSingleFlightPolling({
    enabled: true,
    visible: pageVisible,
    intervalMs: Math.max(10, liveRefreshSec) * 1000,
    run: pollRunner,
    backoffMultiplier: 1.5,
    minBackoffMs: Math.max(10, liveRefreshSec) * 1000,
    maxBackoffMs: 60000,
  });

  const orderedEventCards = useMemo(() => {
    return favoriteEvents.map((eventKey) => eventCards[eventKey]).filter((value): value is FavoriteEventCard => Boolean(value));
  }, [eventCards, favoriteEvents]);

  const orderedTeamCards = useMemo(() => {
    return favoriteTeams.map((teamKey) => teamCards[teamKey]).filter((value): value is FavoriteTeamCard => Boolean(value));
  }, [favoriteTeams, teamCards]);

  const visibleEventCards = useMemo(
    () => orderedEventCards.slice(0, Math.max(1, visibleEventCardCount)),
    [orderedEventCards, visibleEventCardCount],
  );

  const visibleTeamCards = useMemo(
    () => orderedTeamCards.slice(0, Math.max(1, visibleTeamCardCount)),
    [orderedTeamCards, visibleTeamCardCount],
  );

  const contextEventOptions = useMemo(() => {
    const byKey = new Map<string, { event_key: string; name: string; start_date: string | null }>();
    for (const card of orderedTeamCards) {
      for (const event of card.registered_events || []) {
        const eventKey = normalizeEventKey(event.event_key || '');
        if (!eventKey) continue;
        if (!byKey.has(eventKey)) {
          byKey.set(eventKey, {
            event_key: eventKey,
            name: event.name || eventKey,
            start_date: event.start_date || null,
          });
        }
      }
    }
    return [...byKey.values()].sort((a, b) => {
      const aDate = a.start_date || '';
      const bDate = b.start_date || '';
      if (aDate !== bDate) return aDate.localeCompare(bDate);
      return a.event_key.localeCompare(b.event_key);
    });
  }, [orderedTeamCards]);

  useEffect(() => {
    if (contextEventOptions.length === 0) return;
    const current = normalizeEventKey(eventContextInput);
    const valid = contextEventOptions.some((event) => event.event_key === current);
    if (!valid) {
      setEventContextInput(contextEventOptions[0]?.event_key || '');
    }
  }, [eventContextInput, contextEventOptions]);

  const liveFavoriteMatches = useMemo(() => {
    const rows: LiveFavoriteMatch[] = [];
    for (const eventKey of favoriteEvents) {
      const card = eventCards[eventKey];
      if (!card) continue;
      for (const match of card.live_matches) {
        rows.push({
          event_key: card.event_key,
          event_name: card.event_name,
          match,
        });
      }
    }
    return rows;
  }, [eventCards, favoriteEvents]);

  function addFavoriteEvent(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const eventKey = normalizeEventKey(eventInput);
    if (!eventKey) return;
    if (!isEventKey(eventKey)) {
      setErrorText('Enter a valid event key (example: 2026txhou).');
      return;
    }
    if (favoriteEvents.includes(eventKey)) {
      setStatusText(`${eventKey} already added.`);
      setEventInput('');
      return;
    }

    persistFavoriteEvents([eventKey, ...favoriteEvents]);
    setEventInput('');
    setStatusText(`Added ${eventKey}.`);
    setErrorText('');
  }

  function addFavoriteTeam(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const teamKey = normalizeTeamKeyInput(teamInput || '');
    if (!teamKey) {
      setErrorText('Enter a valid team key or number (example: frc118 or 118).');
      return;
    }
    if (favoriteTeams.includes(teamKey)) {
      setStatusText(`${teamKey} already added.`);
      setTeamInput('');
      return;
    }

    persistFavoriteTeams([teamKey, ...favoriteTeams]);
    setTeamInput('');
    setStatusText(`Added ${teamKey}.`);
    setErrorText('');
  }

  function removeFavoriteEvent(eventKey: string) {
    persistFavoriteEvents(favoriteEvents.filter((value) => value !== eventKey));
    setStatusText(`Removed ${eventKey}.`);
  }

  function removeFavoriteTeam(teamKey: string) {
    persistFavoriteTeams(favoriteTeams.filter((value) => value !== teamKey));
    setStatusText(`Removed ${teamKey}.`);
  }

  function openEventCenter(eventKey: string) {
    navigate(`/events?event=${eventKey}`);
  }

  function openMatchCenter(eventKey: string, matchKey: string) {
    const normalizedEventKey = normalizeEventKey(eventKey);
    navigate(buildMatchCenterPath(normalizedEventKey, matchKey));
  }

  function openTeamCenter(teamKey: string) {
    const params = new URLSearchParams();
    params.set('team', teamKey);
    if (eventContextKey) params.set('event', eventContextKey);
    navigate(`/team-center?${params.toString()}`);
  }

  const renderEventCards = (
    <div className="favorites-card-grid">
      {orderedEventCards.length === 0 ? (
        <p className="center-callout muted">No favorite events yet.</p>
      ) : null}
      {visibleEventCards.map((card) => {
        const nextMatch = card.upcoming_matches[0] || null;
        return (
          <article key={`favorite-event-card-${card.event_key}`} className="favorites-item-card event">
            <header>
              <strong>{eventDisplayName(card)}</strong>
              <small>{card.event_key}</small>
            </header>
            <div className="center-status-row compact">
              <span className="center-chip">{card.schedule_count} matches</span>
              <span className="center-chip">{card.teams_count} teams</span>
              <span className="center-chip">{relativeFromTimestamp(card.last_updated_at)}</span>
            </div>

            {card.live_matches.length > 0 ? (
              <div className="center-stack-gap">
                {card.live_matches.slice(0, 2).map((match) => {
                  const timer = liveTimerLabel(match.scheduled_time, nowMs);
                  return (
                    <button
                      key={`favorite-live-${card.event_key}-${match.match_key}`}
                      type="button"
                      className="favorites-match-row"
                      onClick={() => openMatchCenter(card.event_key, match.match_key)}
                    >
                      <span>{match.display_name || match.match_key}</span>
                      <small>
                        {titleizeKey(timer.state)} · {timer.label}: {timer.value}
                      </small>
                    </button>
                  );
                })}
              </div>
            ) : nextMatch ? (
              <p className="center-callout muted">
                Next match: {nextMatch.display_name || nextMatch.match_key} · {fmtDateShort(nextMatch.scheduled_time)}
              </p>
            ) : (
              <p className="center-callout muted">No schedule rows.</p>
            )}

            {card.warnings.length > 0 ? (
              <div className="center-stack-gap">
                {card.warnings.slice(0, 2).map((warning, idx) => (
                  <p key={`favorite-event-warning-${card.event_key}-${idx}`} className="center-callout warning">
                    {warning}
                  </p>
                ))}
              </div>
            ) : null}

            <div className="center-actions-row">
              <button type="button" className="center-btn ghost" onClick={() => openEventCenter(card.event_key)}>
                Event Center
              </button>
              <button type="button" className="center-btn ghost" onClick={() => removeFavoriteEvent(card.event_key)}>
                Remove
              </button>
            </div>
          </article>
        );
      })}
      {orderedEventCards.length > visibleEventCards.length ? (
        <div className="center-actions-row">
          <button
            type="button"
            className="center-btn ghost"
            onClick={() => setVisibleEventCardCount((prev) => prev + 20)}
          >
            Show More Events ({visibleEventCards.length}/{orderedEventCards.length})
          </button>
        </div>
      ) : null}
    </div>
  );

  const renderTeamCards = (
    <div className="favorites-card-grid">
      {orderedTeamCards.length === 0 ? <p className="center-callout muted">No favorite teams yet.</p> : null}
      {visibleTeamCards.map((card) => (
        <article key={`favorite-team-card-${card.team_key}`} className="favorites-item-card team">
          <header>
            <strong>
              {card.team_number !== null ? `#${card.team_number}` : card.team_key} {card.nickname}
            </strong>
            <small>{card.team_key}</small>
          </header>

          {card.error ? <p className="center-callout danger">{card.error}</p> : null}
          <div className="center-status-row compact">
            <span className={`center-chip freshness ${card.freshness_state}`}>Data: {card.freshness_label}</span>
          </div>
          {card.freshness_detail ? (
            <p className={`center-callout ${card.freshness_state === 'stale' ? 'warning' : 'muted'}`}>
              {card.freshness_detail}
            </p>
          ) : null}

            <div className="center-kpi-grid">
              <div className="center-kpi-card">
                <span>Rating</span>
                <strong>{metric(card.rating_0_100, 1)}</strong>
            </div>
            <div className="center-kpi-card">
              <span>Confidence</span>
              <strong>{pct(card.confidence_0_1, 1)}</strong>
            </div>
            <div className="center-kpi-card">
              <span>Fuel</span>
              <strong>{metric(card.fuel_rate, 2)}</strong>
            </div>
            <div className="center-kpi-card">
              <span>Cycle</span>
              <strong>{metricUnit(card.cycle_time_sec, 2, 's')}</strong>
            </div>
            <div className="center-kpi-card">
              <span>Auto</span>
              <strong>{metric(card.auto_contribution, 2)}</strong>
            </div>
              <div className="center-kpi-card">
                <span>Climb</span>
                <strong>{pct(card.climb_success_prob, 1)}</strong>
              </div>
              <div className="center-kpi-card">
                <span>Robot</span>
                <strong>{metric(card.rating_robot_level_0_100, 1)}</strong>
              </div>
              <div className="center-kpi-card">
                <span>TBA Rank</span>
                <strong>{card.tba_rank !== null ? `#${card.tba_rank}` : 'N/A'}</strong>
              </div>
            </div>

            <div className="center-status-row compact">
              <span className="center-chip">{card.matches_analyzed} analyzed</span>
              <span className="center-chip">{card.events_count} events</span>
              <span className="center-chip">Driver {metric(card.rating_driver_skill_0_100, 1)}</span>
              <span className="center-chip">{card.tba_record}</span>
              <span className="center-chip">{relativeFromTimestamp(card.last_updated_at)}</span>
            </div>

          {card.warnings.length > 0 ? (
            <div className="center-stack-gap">
              {card.warnings.slice(0, 2).map((warning, idx) => (
                <p key={`favorite-team-warning-${card.team_key}-${idx}`} className="center-callout warning">
                  {warning}
                </p>
              ))}
            </div>
          ) : null}

          <div className="center-actions-row">
            <button type="button" className="center-btn ghost" title={`Open Team Center for ${card.team_key}`} onClick={() => openTeamCenter(card.team_key)}>
              Team Details
            </button>
            <button type="button" className="center-btn ghost" onClick={() => removeFavoriteTeam(card.team_key)}>
              Remove
            </button>
          </div>
        </article>
      ))}
      {orderedTeamCards.length > visibleTeamCards.length ? (
        <div className="center-actions-row">
          <button
            type="button"
            className="center-btn ghost"
            onClick={() => setVisibleTeamCardCount((prev) => prev + 20)}
          >
            Show More Teams ({visibleTeamCards.length}/{orderedTeamCards.length})
          </button>
        </div>
      ) : null}
    </div>
  );


  return (
    <div className={`favorites-layout-grid mobile-finder-layout ${isMobileLayout && mobileFinderOpen ? 'mobile-finder-open' : ''}`.trim()}>
      {isMobileLayout ? (
        <SegmentedTabs
          className="mobile-view-toggle"
          itemClassName="mobile-view-toggle-btn"
          ariaLabel="Favorites mobile view switch"
          value={mobileFinderOpen ? 'controls' : 'favorites'}
          onChange={(next) => setMobileFinderOpen(next === 'controls')}
          items={[
            { value: 'controls', label: 'Favorites Controls' },
            { value: 'favorites', label: 'Favorites View' },
          ]}
        />
      ) : null}
      <aside className="center-sidebar">
        <SurfaceCard
          title="Favorite Manager"
          subtitle="Pin teams and events, set rating context."
          className="favorites-manager-card"
        >
          <form className="center-input-row" onSubmit={addFavoriteEvent}>
            <input
              value={eventInput}
              onChange={(event) => setEventInput(event.target.value)}
              placeholder="Add event key"
              aria-label="Add favorite event"
            />
            <button type="submit" className="center-btn">
              Add Event
            </button>
          </form>

          <form className="center-input-row" onSubmit={addFavoriteTeam}>
            <input
              value={teamInput}
              onChange={(event) => setTeamInput(event.target.value)}
              placeholder="Add team key/number"
              aria-label="Add favorite team"
            />
            <button type="submit" className="center-btn">
              Add Team
            </button>
          </form>

          <label className="center-label" htmlFor="favorites-context-event">
            Team Rating Context Event (optional)
          </label>
          <div className="center-input-row">
            <select
              id="favorites-context-event"
              value={eventContextInput}
              onChange={(event) => setEventContextInput(normalizeEventKey(event.target.value))}
            >
              <option value="">Auto-select from team events</option>
              {contextEventOptions.map((event) => (
                <option key={`favorites-context-option-${event.event_key}`} value={event.event_key}>
                  {event.name} ({event.event_key})
                </option>
              ))}
            </select>
            <button
              type="button"
              className="center-btn ghost"
              onClick={() => setEventContextInput(readStoredCenterContext().eventKey)}
            >
              Use Active
            </button>
          </div>

          <div className="center-actions-row">
            <button
              type="button"
              className="center-btn ghost"
              onClick={() => void refreshFavorites({ forceNetwork: true })}
              disabled={loadingEvents || loadingTeams}
            >
              {loadingEvents || loadingTeams ? 'Refreshing...' : 'Refresh Favorites'}
            </button>
            <Link className="center-btn ghost" to="/settings">
              Settings
            </Link>
          </div>

          <div className="center-status-row compact">
            <span className="center-chip">{relativeFromTimestamp(lastUpdatedAt)}</span>
          </div>

          {errorText ? <p className="center-callout danger">{errorText}</p> : null}
          <p className="center-callout muted">{statusText}</p>
        </SurfaceCard>

        <SurfaceCard title="Snapshot" subtitle="Favorites watchlist health." className="favorites-snapshot-card">
          <div className="center-status-row compact">
            <span className="center-chip">{favoriteEvents.length} events</span>
            <span className="center-chip">{favoriteTeams.length} teams</span>
            <span className="center-chip">{liveFavoriteMatches.length} live</span>
          </div>

          {liveFavoriteMatches.length > 0 ? (
            <div className="center-list-scroll" role="list" aria-label="Live favorite matches">
              {liveFavoriteMatches.slice(0, 8).map((row) => {
                const timer = liveTimerLabel(row.match.scheduled_time, nowMs);
                return (
                  <button
                    type="button"
                    key={`favorite-live-row-${row.event_key}-${row.match.match_key}`}
                    className="event-picker-item match-picker-item"
                    onClick={() => openMatchCenter(row.event_key, row.match.match_key)}
                  >
                    <strong>{row.match.display_name || row.match.match_key}</strong>
                    <small>{row.event_name}</small>
                    <small>
                      {timer.label}: {timer.value}
                    </small>
                  </button>
                );
              })}
            </div>
          ) : (
            <p className="center-callout muted">No live matches.</p>
          )}
        </SurfaceCard>
      </aside>

      <section className="center-main">
        <SurfaceCard title="Favorites" subtitle="Tracked teams and events." className="favorites-header-card">
          <div className="center-tabs-header">
            <SegmentedTabs
              className="center-tabs"
              itemClassName="center-tab-btn"
              ariaLabel="Favorites tabs"
              value={activeTab}
              onChange={setActiveTab}
              items={(['overview', 'events', 'teams'] as const).map((tab) => ({
                value: tab,
                label: titleizeKey(tab),
              }))}
            />
          </div>
        </SurfaceCard>

        {activeTab === 'overview' ? (
          <SurfaceCardGroup groupId="favorites-overview">
            <div className="favorites-overview-grid">
              <SurfaceCard
                title="Favorite Events"
                subtitle="Pinned events status."
                right={<span className="center-chip">{loadingEvents ? 'Loading...' : `${orderedEventCards.length} loaded`}</span>}
                className="favorites-events-shell"
                compactable
              >
                {renderEventCards}
              </SurfaceCard>
              <SurfaceCard
                title="Favorite Teams"
                subtitle="Team profiles with event-context ratings."
                right={<span className="center-chip">{loadingTeams ? 'Loading...' : `${orderedTeamCards.length} loaded`}</span>}
                className="favorites-teams-shell"
                compactable
              >
                {renderTeamCards}
              </SurfaceCard>
            </div>
          </SurfaceCardGroup>
        ) : null}

        {activeTab === 'events' ? (
          <SurfaceCardGroup groupId="favorites-events">
            <SurfaceCard
              title="Events Watchlist"
              subtitle="Schedule readiness and live windows."
              right={<span className="center-chip">{loadingEvents ? 'Loading...' : `${orderedEventCards.length} events`}</span>}
              className="favorites-events-shell"
              compactable
            >
              {renderEventCards}
            </SurfaceCard>
          </SurfaceCardGroup>
        ) : null}

        {activeTab === 'teams' ? (
          <SurfaceCardGroup groupId="favorites-teams">
            <SurfaceCard
              title="Teams Watchlist"
              subtitle="Compare core targets."
              right={<span className="center-chip">{loadingTeams ? 'Loading...' : `${orderedTeamCards.length} teams`}</span>}
              className="favorites-teams-shell"
              compactable
            >
              {renderTeamCards}
            </SurfaceCard>
          </SurfaceCardGroup>
        ) : null}
      </section>
    </div>
  );
}
