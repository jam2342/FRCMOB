import { type FormEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useEventNames } from '../hooks/useEventNames';
import { eventDisplayName } from '../utils/eventSearchIndex';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import {
  getEventSchedule,
  getEventTeamLiveForm,
  getEventTeamsIntel,
  getTeamIntel,
} from '../api';
import type { EventScheduleItem, TeamCompetitionsResponse } from '../api';
import { SegmentedTabs } from '../components/ui/SegmentedTabs';
import { EventPicker } from '../components/EventPicker';
import { MobileViewActions } from '../components/ui/MobileViewActions';
import { SurfaceCard } from '../components/ui/SurfaceCard';
import {
  Button,
  CardBody,
  CardGrid,
  Chip,
  FieldSelect,
  FieldFrame,
  FieldText,
  Stat,
} from '../components/ui/primitives';
import styles from './FavoritesPage.module.css';
import { useLiveRefreshSetting } from '../hooks/useLiveRefreshSetting';
import { useExternalSearchSync } from '../hooks/useExternalSearchSync';
import { useMobileLayout } from '../hooks/useMobileLayout';
import { usePageClock } from '../hooks/usePageClock';
import { usePageVisibility } from '../hooks/usePageVisibility';
import { resolveTab } from './tabUtils';
import { useSingleFlightPolling } from '../hooks/useSingleFlightPolling';
import {
  asRecord,
  buildMatchCenterPath,
  CURRENT_SEASON_YEAR,
  fmtDateShort,
  liveTimerLabel, matchStartTime,
  metric,
  normalizeTeamKeyInput,
  parseNumber,
  pct,
  relativeFromTimestamp,
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
import { showToast } from '../components/ui/toast';
import { MISSING } from '../utils/formatValue';
import { EmptyState } from '../components/ui/EmptyState';

type FavoritesTab = 'overview' | 'events' | 'teams';

type FavoriteEventCard = {
  event_key: string;
  event_name: string;
  schedule_count: number | null;
  teams_count: number | null;
  completed_count: number | null;
  live_team_count: number | null;
  schedule_available: boolean;
  stale: boolean;
  live_matches: EventScheduleItem[];
  upcoming_matches: EventScheduleItem[];
  warnings: string[];
  last_updated_at: number;
};

type FavoriteTeamCard = {
  team_key: string;
  team_number: number | null;
  nickname: string;
  fuel_rate: number | null;
  auto_contribution: number | null;
  climb_success_prob: number | null;
  rating_0_100: number | null;
  confidence_0_1: number | null;
  rating_points_per_match: number | null;
  tba_rank: number | null;
  tba_record: string;
  tba_event_awards_count: number | null;
  tba_year_awards_count: number | null;
  events_count: number | null;
  event_context_key: string;
  data_available: boolean;
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

const FAVORITES_TABS = ['overview', 'events', 'teams'] as const;

// A failed load still needs to say which event this is: the shared index name, else "2026 ARC".
function eventCardName(card: FavoriteEventCard): string {
  return card.event_name && card.event_name !== card.event_key ? card.event_name : eventDisplayName(card.event_key);
}

export function FavoritesPage() {
  // Re-renders once the shared event index loads, so fallbacks become names.
  useEventNames();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const isMobileLayout = useMobileLayout();
  const pageVisible = usePageVisibility();
  const nowMs = usePageClock(pageVisible);
  const liveRefreshSec = useLiveRefreshSetting();
  const tabParam = searchParams.get('tab');
  const defaultTab = resolveTab(tabParam, FAVORITES_TABS, 'overview');

  const [activeTab, setActiveTab] = useState<FavoritesTab>(defaultTab);
  const [favoriteEvents, setFavoriteEvents] = useState<string[]>(() => readFavoriteEvents());
  const [favoriteTeams, setFavoriteTeams] = useState<string[]>(() => readFavoriteTeams());
  const nothingPinned = favoriteEvents.length === 0 && favoriteTeams.length === 0;

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
  const previousScopeRef = useRef(JSON.stringify([activeTab, favoriteEvents, favoriteTeams, eventContextInput]));
  const tabRefreshPendingRef = useRef(false);
  const pollTriggerRef = useRef<() => void>(() => {});

  useEffect(() => {
    if (!isMobileLayout) setMobileFinderOpen(false);
  }, [isMobileLayout]);

  const urlSync = useExternalSearchSync(searchParams, (params) => {
    const urlTab = params.get('tab');
    setActiveTab(resolveTab(urlTab, FAVORITES_TABS, 'overview'));
  });

  useEffect(() => {
    if (!urlSync.shouldWrite()) return;
    const next = new URLSearchParams(searchParams);
    if (activeTab === 'overview') {
      next.delete('tab');
    } else {
      next.set('tab', activeTab);
    }
    // Returning the current params from a router updater still navigates. Avoid
    // calling it at all when the URL is unchanged, including during route exits.
    if (next.toString() === searchParams.toString()) return;
    urlSync.markWritten(next.toString());
    setSearchParams(next, { replace: true });
  }, [activeTab, searchParams, setSearchParams, urlSync]);

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

  const refreshFavorites = useCallback(async (options?: { forceNetwork?: boolean }): Promise<boolean> => {
    if (refreshInFlightRef.current) return true;
    refreshInFlightRef.current = true;
    const forceNetwork = Boolean(options?.forceNetwork);
    const eventsToRefresh = activeTab === 'teams' ? [] : favoriteEvents;
    const teamsToRefresh = activeTab === 'events' ? [] : favoriteTeams;
    setErrorText('');
    try {
      if (favoriteEvents.length === 0 && favoriteTeams.length === 0) {
        setEventCards({});
        setTeamCards({});
        setStatusText('No favorites yet.');
        setLastUpdatedAt(Date.now());
        return true;
      }

      setLoadingEvents(activeTab !== 'teams');
      setLoadingTeams(activeTab !== 'events');
      setStatusText('Refreshing…');

      const eventResults = await Promise.allSettled(
        eventsToRefresh.map(async (eventKey) => {
          const warnings: string[] = [];
          const [scheduleResult, teamsResult, liveFormResult] = await Promise.allSettled([
            getEventSchedule(eventKey, forceNetwork, undefined, { bypassCache: forceNetwork }),
            getEventTeamsIntel(
              eventKey,
              {
                include_tba: true,
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
        let teamsCount: number | null = null;
        let liveTeamCount: number | null = null;

        if (scheduleResult.status === 'fulfilled') {
          scheduleMatches = scheduleResult.value.matches || [];
          eventName = scheduleResult.value.event_name || eventName;
        } else {
          warnings.push('Schedule unavailable: try refreshing again.');
        }

        if (teamsResult.status === 'fulfilled') {
          teamsCount = teamsResult.value.teams_count ?? teamsResult.value.teams?.length ?? null;
          if (!eventName || eventName === eventKey) {
            eventName = teamsResult.value.event_name || eventName;
          }
          for (const warning of teamsResult.value.warnings || []) {
            warnings.push(`Intel: ${warning}`);
          }
        } else {
          warnings.push('Teams unavailable: try refreshing again.');
        }

        if (liveFormResult.status === 'fulfilled') {
          const statuses = Object.values(liveFormResult.value.team_statuses || {});
          liveTeamCount = statuses.filter((item) => item?.is_live).length;
        } else {
          warnings.push('Live updates unavailable: try refreshing again.');
        }

        const liveMatches: EventScheduleItem[] = [];
        const upcomingMatches: EventScheduleItem[] = [];
        let completedCount = 0;

        for (const match of scheduleMatches) {
          if (match.is_completed) {
            completedCount += 1;
            continue;
          }
          const timer = liveTimerLabel(matchStartTime(match), Date.now());
          if (timer.state === 'live') liveMatches.push(match);
          if (timer.state === 'upcoming') upcomingMatches.push(match);
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
          schedule_count: scheduleResult.status === 'fulfilled' ? scheduleMatches.length : null,
          teams_count: teamsCount,
          completed_count: scheduleResult.status === 'fulfilled' ? completedCount : null,
          live_team_count: liveTeamCount,
          schedule_available: scheduleResult.status === 'fulfilled',
          stale: false,
          live_matches: liveMatches,
          upcoming_matches: upcomingMatches,
          warnings,
          last_updated_at: Date.now(),
        };

          return card;
        }),
      );

      const teamResults = await Promise.allSettled(
        teamsToRefresh.map(async (teamKey) => {
          const warnings: string[] = [];

        let error = '';
        let teamNumber = teamNumberFromTeamKey(teamKey);
        let nickname = '';
        let fuelRate: number | null = null;
        let autoContribution: number | null = null;
        let climbSuccessProb: number | null = null;
        let eventsCount: number | null = null;
        let nextEventKey: string | null = null;
        let rating: number | null = null;
        let confidence: number | null = null;
        let ratingPoints: number | null = null;
        let tbaRank: number | null = null;
        let tbaRecord = MISSING;
        let tbaEventAwardsCount: number | null = null;
        let tbaYearAwardsCount: number | null = null;
        let registeredEvents: TeamCompetitionsResponse['registered_events'] = [];

          try {
            const intel = await getTeamIntel(
              teamKey,
              {
                event_key: eventContextKey || undefined,
                preferred_year: CURRENT_SEASON_YEAR,
                fallback_year: CURRENT_SEASON_YEAR - 1,
                include_tba: true,
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
          fuelRate = parseNumber(averages?.fuel_scoring_rate);
          autoContribution = parseNumber(averages?.auto_contribution);
          climbSuccessProb = parseNumber(averages?.climb_success_prob);

          eventsCount = parseNumber(competitions?.registered_events_count);
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
          ratingPoints = parseNumber(asRecord(ratingPayload?.expected_points)?.total);

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
        } catch {
          error = 'Team data unavailable. Try refreshing again.';
        }

          const card: FavoriteTeamCard = {
            team_key: teamKey,
            team_number: teamNumber,
            nickname,
            fuel_rate: fuelRate,
            auto_contribution: autoContribution,
            climb_success_prob: climbSuccessProb,
            rating_0_100: rating,
            confidence_0_1: confidence,
            rating_points_per_match: ratingPoints,
            tba_rank: tbaRank,
            tba_record: tbaRecord,
            tba_event_awards_count: tbaEventAwardsCount,
            tba_year_awards_count: tbaYearAwardsCount,
            events_count: eventsCount,
            event_context_key: eventContextKey,
            data_available: !error,
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
        const key = eventsToRefresh[index];
        if (!key) continue;
        if (result.status === 'fulfilled') {
          nextEventCards[key] = result.value;
          errors.push(...result.value.warnings.filter((warning) => warning.includes('unavailable:')));
        } else {
          errors.push('Could not refresh a saved event.');
        }
      }

      for (let index = 0; index < teamResults.length; index += 1) {
        const result = teamResults[index];
        const key = teamsToRefresh[index];
        if (!key) continue;
        if (result.status === 'fulfilled') {
          nextTeamCards[key] = result.value;
          if (result.value.error) errors.push(result.value.error);
        } else {
          errors.push('Could not refresh a saved team.');
        }
      }

      if (activeTab !== 'teams') setEventCards((previous) => {
        for (const [key, card] of Object.entries(nextEventCards)) {
          const old = previous[key];
          if (!old) continue;
          const retainSchedule = !card.schedule_available && old.schedule_count != null;
          const retainTeams = card.teams_count == null && old.teams_count != null;
          const retainLive = card.live_team_count == null && old.live_team_count != null;
          nextEventCards[key] = {
            ...card,
            event_name: card.event_name === key ? old.event_name : card.event_name,
            ...(retainSchedule ? {
              schedule_count: old.schedule_count,
              completed_count: old.completed_count,
              live_matches: old.live_matches,
              upcoming_matches: old.upcoming_matches,
            } : {}),
            teams_count: retainTeams ? old.teams_count : card.teams_count,
            live_team_count: retainLive ? old.live_team_count : card.live_team_count,
            stale: retainSchedule || retainTeams || retainLive,
            last_updated_at: retainSchedule || retainTeams || retainLive ? old.last_updated_at : card.last_updated_at,
          };
        }
        return { ...nextEventCards };
      });
      if (activeTab !== 'events') setTeamCards((previous) => {
        for (const [key, card] of Object.entries(nextTeamCards)) {
          const old = previous[key];
          if (card.error && old?.data_available && old.event_context_key === card.event_context_key) {
            nextTeamCards[key] = { ...old, error: `${card.error} Showing saved data; it may be out of date.` };
          }
        }
        return { ...nextTeamCards };
      });
      setLastUpdatedAt(Date.now());

      if (errors.length > 0) {
        setErrorText(errors.join(' | '));
        setStatusText('Partial data loaded.');
      } else {
        setStatusText('Favorites refreshed.');
      }
      return errors.length === 0;
    } catch (error) {
      setErrorText((error as Error).message || 'Favorites refresh failed.');
      setStatusText('Refresh failed.');
      return false;
    } finally {
      setLoadingEvents(false);
      setLoadingTeams(false);
      refreshInFlightRef.current = false;
      if (tabRefreshPendingRef.current) {
        tabRefreshPendingRef.current = false;
        window.setTimeout(() => pollTriggerRef.current(), 0);
      }
    }
  }, [activeTab, eventContextKey, favoriteEvents, favoriteTeams]);

  const pollRunner = useCallback(() => refreshFavorites(), [refreshFavorites]);

  const { triggerNow } = useSingleFlightPolling({
    enabled: true,
    visible: pageVisible,
    intervalMs: Math.max(10, liveRefreshSec) * 1000,
    run: pollRunner,
    backoffMultiplier: 1.5,
    minBackoffMs: Math.max(10, liveRefreshSec) * 1000,
    maxBackoffMs: 60000,
  });

  pollTriggerRef.current = triggerNow;
  useEffect(() => {
    const scope = JSON.stringify([activeTab, favoriteEvents, favoriteTeams, eventContextInput]);
    if (previousScopeRef.current === scope) return;
    previousScopeRef.current = scope;
    if (refreshInFlightRef.current) tabRefreshPendingRef.current = true;
    else triggerNow();
  }, [activeTab, favoriteEvents, favoriteTeams, eventContextInput, triggerNow]);

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
            name: event.name || eventDisplayName(eventKey),
            start_date: event.start_date || null,
          });
        }
      }
    }
    const activeEvent = normalizeEventKey(eventContextInput);
    if (activeEvent && !byKey.has(activeEvent)) byKey.set(activeEvent, {event_key: activeEvent, name: eventDisplayName(activeEvent), start_date: null});
    return [...byKey.values()].sort((a, b) => {
      const aDate = a.start_date || '';
      const bDate = b.start_date || '';
      if (aDate !== bDate) return aDate.localeCompare(bDate);
      return a.event_key.localeCompare(b.event_key);
    });
  }, [orderedTeamCards, eventContextInput]);


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

  function addFavoriteEvent(rawKey: string) {
    const eventKey = normalizeEventKey(rawKey);
    if (!eventKey) return;
    if (!isEventKey(eventKey)) {
      setErrorText('Pick an event from the list.');
      return;
    }
    if (favoriteEvents.includes(eventKey)) {
      setStatusText('This event is already saved.');
      setEventInput('');
      return;
    }

    persistFavoriteEvents([eventKey, ...favoriteEvents]);
    if (activeTab === 'teams') setActiveTab('events');
    setEventInput('');
    setStatusText('Event added.');
    setErrorText('');
  }

  function addFavoriteTeam(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const teamKey = normalizeTeamKeyInput(teamInput || '');
    if (!teamKey) {
      setErrorText('Enter a valid team number, such as 118.');
      return;
    }
    if (favoriteTeams.includes(teamKey)) {
      setStatusText(`Team ${teamNumberFromTeamKey(teamKey)} is already saved.`);
      setTeamInput('');
      return;
    }

    persistFavoriteTeams([teamKey, ...favoriteTeams]);
    if (activeTab === 'events') setActiveTab('teams');
    setTeamInput('');
    setStatusText(`Added Team ${teamNumberFromTeamKey(teamKey)}.`);
    setErrorText('');
  }

  // Undo puts it back in the same place; read the stored list then, in case something else
  // changed it while the toast was up.
  function removeFavoriteEvent(eventKey: string) {
    const position = favoriteEvents.indexOf(eventKey);
    persistFavoriteEvents(favoriteEvents.filter((value) => value !== eventKey));
    setStatusText('');
    showToast({
      message: `Removed ${eventDisplayName(eventKey)} from favorites`,
      actionLabel: 'Undo',
      onAction: () => {
        const current = readFavoriteEvents().filter((value) => value !== eventKey);
        current.splice(Math.max(0, Math.min(position, current.length)), 0, eventKey);
        persistFavoriteEvents(current);
      },
    });
  }

  function removeFavoriteTeam(teamKey: string) {
    const position = favoriteTeams.indexOf(teamKey);
    persistFavoriteTeams(favoriteTeams.filter((value) => value !== teamKey));
    setStatusText('');
    showToast({
      message: `Removed Team ${teamNumberFromTeamKey(teamKey)} from favorites`,
      actionLabel: 'Undo',
      onAction: () => {
        const current = readFavoriteTeams().filter((value) => value !== teamKey);
        current.splice(Math.max(0, Math.min(position, current.length)), 0, teamKey);
        persistFavoriteTeams(current);
      },
    });
  }

  function openMatchCenter(eventKey: string, matchKey: string) {
    const normalizedEventKey = normalizeEventKey(eventKey);
    navigate(buildMatchCenterPath(normalizedEventKey, matchKey));
  }

  function teamCenterPath(teamKey: string) {
    const params = new URLSearchParams();
    params.set('team', teamKey);
    if (eventContextKey) params.set('event', eventContextKey);
    return `/team-center?${params.toString()}`;
  }

  const renderEventCards = (
    <div className="favorites-card-grid">
      {favoriteEvents.filter(key => !eventCards[key]).map(key => <p key={key} role="status">Loading {eventDisplayName(key)}…</p>)}
      {favoriteEvents.length === 0 ? (
        <EmptyState compact title="No favorite events yet" description={isMobileLayout ? 'Tap "Add or edit" to pin one.' : 'Add one in Favorite Manager to start tracking it.'} />
      ) : null}
      {visibleEventCards.map((card) => {
        const nextMatch = card.upcoming_matches[0] || null;
        return (
          <article key={`favorite-event-card-${card.event_key}`} className="favorites-item-card event">
            <header>
              <strong><Link className="entity-link" to={`/events?event=${encodeURIComponent(card.event_key)}`}>{eventCardName(card)}</Link></strong>
            </header>
            <div className={styles.chipRow}>
              <Chip>{card.schedule_count == null ? 'Matches unavailable' : `${card.schedule_count} match${card.schedule_count === 1 ? '' : 'es'}`}</Chip>
              <Chip>{card.teams_count == null ? 'Teams unavailable' : `${card.teams_count} team${card.teams_count === 1 ? '' : 's'}`}</Chip>
              <Chip>{relativeFromTimestamp(card.last_updated_at)}</Chip>
            </div>
            {card.stale ? <p className={`${styles.note} ${styles.noteWarning}`}>Showing saved data; it may be out of date.</p> : null}

            {card.live_matches.length > 0 ? (
              <div className="center-stack-gap">
                {card.live_matches.slice(0, 2).map((match) => {
                  const timer = liveTimerLabel(matchStartTime(match), nowMs);
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
              <p className={styles.note}>
                Next match: <Link className="entity-link" to={buildMatchCenterPath(card.event_key, nextMatch.match_key)}>{nextMatch.display_name || nextMatch.match_key}</Link> · {fmtDateShort(matchStartTime(nextMatch))}
              </p>
            ) : (
              <p className={styles.note}>{!card.schedule_available ? 'Schedule unavailable.' : card.schedule_count === 0 ? 'Schedule not published yet.' : card.completed_count === card.schedule_count ? 'All published matches are complete.' : 'No upcoming matches.'}</p>
            )}

            {card.warnings.length > 0 ? (
              <div className="center-stack-gap">
                {card.warnings.slice(0, 2).map((warning, idx) => (
                  <p key={`favorite-event-warning-${card.event_key}-${idx}`} className={`${styles.note} ${styles.noteWarning}`}>
                    {warning}
                  </p>
                ))}
              </div>
            ) : null}

            <div className={styles.actions}>
              <Link className="entity-link" to={`/events?event=${encodeURIComponent(card.event_key)}&tab=rankings`} aria-label={`Rankings for ${eventCardName(card)}`}>Rankings</Link>
              <Button size="sm" variant="quiet" onClick={() => removeFavoriteEvent(card.event_key)}>
                Remove
              </Button>
            </div>
          </article>
        );
      })}
      {orderedEventCards.length > visibleEventCards.length ? (
        <div className={styles.actions}>
          <Button variant="quiet" fullWidth onClick={() => setVisibleEventCardCount((prev) => prev + 20)}>
            Show More Events ({visibleEventCards.length}/{orderedEventCards.length})
          </Button>
        </div>
      ) : null}
    </div>
  );

  const renderTeamCards = (
    <div className="favorites-card-grid">
      {favoriteTeams.filter(key => !teamCards[key]).map(key => <p key={key} role="status">Loading team {key.replace(/^frc/, '')}…</p>)}
      {favoriteTeams.length === 0 ? (
        <EmptyState compact title="No favorite teams yet" description={isMobileLayout ? 'Tap "Add or edit" to pin one.' : 'Add one in Favorite Manager to start tracking it.'} />
      ) : null}
      {visibleTeamCards.map((card) => (
        <article key={`favorite-team-card-${card.team_key}`} className="favorites-item-card team">
          <header>
            <strong>
              <Link className="entity-link" to={teamCenterPath(card.team_key)}>
                {card.team_number !== null ? `#${card.team_number}` : `Team ${card.team_key.replace(/^frc/, '')}`} {card.nickname}
              </Link>
            </strong>
          </header>

          {card.error ? (
            <p className={`${styles.note} ${styles.noteDanger}`} role="alert">{card.error}</p>
          ) : null}

            <CardGrid dense>
              <Stat label="Rating" value={metric(card.rating_0_100, 1)} size="sm" />
              <Stat label="Confidence" value={pct(card.confidence_0_1, 1)} size="sm" />
              <Stat label="Fuel" value={metric(card.fuel_rate, 1)} size="sm" />
              <Stat label="Auto" value={metric(card.auto_contribution, 1)} size="sm" />
              <Stat label="Climb" value={pct(card.climb_success_prob, 1)} size="sm" />
              <Stat label="Pts / match" value={metric(card.rating_points_per_match, 0)} size="sm" />
              <Stat label="TBA Rank" value={card.tba_rank !== null ? `#${card.tba_rank}` : MISSING} size="sm" />
            </CardGrid>

            <div className={styles.chipRow}>
              <Chip>{card.events_count == null ? 'Events unavailable' : `${card.events_count} event${card.events_count === 1 ? '' : 's'}`}</Chip>
              <Chip>{card.tba_record}</Chip>
              <Chip>{relativeFromTimestamp(card.last_updated_at)}</Chip>
            </div>

          {card.warnings.length > 0 ? (
            <div className="center-stack-gap">
              {card.warnings.slice(0, 2).map((warning, idx) => (
                <p key={`favorite-team-warning-${card.team_key}-${idx}`} className={`${styles.note} ${styles.noteWarning}`}>
                  {warning}
                </p>
              ))}
            </div>
          ) : null}

          <div className={styles.actions}>
            <Button size="sm" variant="quiet" onClick={() => removeFavoriteTeam(card.team_key)}>
              Remove
            </Button>
          </div>
        </article>
      ))}
      {orderedTeamCards.length > visibleTeamCards.length ? (
        <div className={styles.actions}>
          <Button variant="quiet" fullWidth onClick={() => setVisibleTeamCardCount((prev) => prev + 20)}>
            Show More Teams ({visibleTeamCards.length}/{orderedTeamCards.length})
          </Button>
        </div>
      ) : null}
    </div>
  );


  return (
    <div className={`favorites-layout-grid mobile-finder-layout ${isMobileLayout && mobileFinderOpen ? 'mobile-finder-open' : ''}`.trim()}>
      {isMobileLayout && mobileFinderOpen ? (
        <MobileViewActions
          label="Favorites views"
          actions={
            mobileFinderOpen
              ? [{ label: 'Back to favorites', onClick: () => setMobileFinderOpen(false), back: true }]
              : [{ label: 'Add or edit', onClick: () => setMobileFinderOpen(true) }]
          }
        />
      ) : null}
      <aside className="center-sidebar">
        <SurfaceCard
          title="Favorite Manager"
          subtitle="Pin teams and events, set rating context."
          className="favorites-manager-card"
        >
          <CardBody>
            {/* Same search-by-name picker as every other event box; it was the last one that
                wanted a raw code. Its button reads "Add event" while typing. */}
            <FieldFrame label="Add event" htmlFor="favorites-add-event">
              <EventPicker
                value=""
                inputValue={eventInput}
                onInputChange={setEventInput}
                onSelect={addFavoriteEvent}
                onSubmit={() => addFavoriteEvent(eventInput)}
                placeholder='Search events, e.g. "houston" or "2026txhou"'
                actionLabel="Add event"
                clearOnSelect
                inputId="favorites-add-event"
              />
            </FieldFrame>

            <form className={styles.addRow} onSubmit={addFavoriteTeam}>
              <FieldText
                label="Add team"
                value={teamInput}
                onChange={(event) => setTeamInput(event.target.value)}
                placeholder="Team key or number"
              />
              <Button type="submit" variant="primary">Add Team</Button>
            </form>

            <div className={styles.addRow}>
              <FieldSelect
                label="Event for team ratings"
                value={eventContextInput}
                onChange={(event) => setEventContextInput(normalizeEventKey(event.target.value))}
              >
                <option value="">Auto-select from team events</option>
                {contextEventOptions.map((event) => (
                  <option key={`favorites-context-option-${event.event_key}`} value={event.event_key}>
                    {event.name}
                  </option>
                ))}
              </FieldSelect>
              <Button
                variant="quiet"
                onClick={() => setEventContextInput(readStoredCenterContext().eventKey)}
              >
                Use Active
              </Button>
            </div>

            <div className={styles.actions}>
              <Button
                variant="quiet"
                onClick={() => void refreshFavorites({ forceNetwork: true })}
                loading={loadingEvents || loadingTeams}
              >
                {loadingEvents || loadingTeams ? 'Refreshing…' : 'Refresh Favorites'}
              </Button>
              <Button as="a" variant="quiet" href="#/settings">
                Settings
              </Button>
            </div>

            <div className={styles.chipRow}>
              <Chip>Updated {relativeFromTimestamp(lastUpdatedAt)}</Chip>
            </div>

            {errorText ? (
              <p className={`${styles.note} ${styles.noteDanger}`} role="alert">{errorText}</p>
            ) : null}
            <p className={styles.note}>{statusText}</p>
          </CardBody>
        </SurfaceCard>

        <SurfaceCard title="Snapshot" className="favorites-snapshot-card">
          <CardGrid dense>
            <Stat label="Events" value={favoriteEvents.length} size="sm" />
            <Stat label="Teams" value={favoriteTeams.length} size="sm" />
            <Stat
              label="Live now"
              value={liveFavoriteMatches.length}
              size="sm"
              tone={liveFavoriteMatches.length > 0 ? 'accent' : 'default'}
            />
          </CardGrid>

          {liveFavoriteMatches.length > 0 ? (
            <div className="center-list-scroll" role="list" aria-label="Live favorite matches">
              {liveFavoriteMatches.slice(0, 8).map((row) => {
                const timer = liveTimerLabel(matchStartTime(row.match), nowMs);
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
            <p className={styles.note}>No live matches.</p>
          )}
        </SurfaceCard>
      </aside>

      <section className="center-main">
        <SurfaceCard
          title="Favorites"
          className="favorites-header-card"
          right={isMobileLayout && !mobileFinderOpen ? (
            <MobileViewActions inline label="Favorites views" actions={[{ label: 'Add or edit', onClick: () => setMobileFinderOpen(true) }]} />
          ) : undefined}
        >
          {nothingPinned && isMobileLayout ? null : (
          <div className="center-tabs-header">
            <SegmentedTabs
              ariaLabel="Favorites tabs"
              value={activeTab}
              onChange={setActiveTab}
              items={FAVORITES_TABS.map((tab) => ({
                value: tab,
                label: titleizeKey(tab),
              }))}
            />
          </div>
          )}
        </SurfaceCard>

        {/* One definition per list, shown in whichever tabs include it. The page
            used to carry two copies of each — "Favorite Events" on the overview
            tab and "Events Watchlist" on its own — which is how two names ended
            up describing one list. There is no watchlist in this app: no
            storage key, no endpoint, no type. Only Favorites. */}

          {nothingPinned && isMobileLayout ? (
            // Three tabs over two empty cards said "nothing here" three times.
            <EmptyState
              title="Nothing pinned yet"
              description="Pin events and teams to follow their matches and ratings here."
              action={<Button onClick={() => setMobileFinderOpen(true)}>Add favorites</Button>}
            />
          ) : (
          <div className={activeTab === 'overview' ? styles.overviewGrid : undefined}>
            {activeTab !== 'teams' ? (
              <SurfaceCard
                title="Favorite Events"
                right={<Chip>{loadingEvents ? 'Loading…' : `${orderedEventCards.length} event${orderedEventCards.length === 1 ? '' : 's'}`}</Chip>}
                compactable
              >
                {renderEventCards}
              </SurfaceCard>
            ) : null}
            {activeTab !== 'events' ? (
              <SurfaceCard
                title="Favorite Teams"
                right={<Chip>{loadingTeams ? 'Loading…' : `${orderedTeamCards.length} team${orderedTeamCards.length === 1 ? '' : 's'}`}</Chip>}
                compactable
              >
                {renderTeamCards}
              </SurfaceCard>
            ) : null}
          </div>
          )}

      </section>
    </div>
  );
}
