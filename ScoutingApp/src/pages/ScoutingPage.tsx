import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import {
  addScoutingRoomSecondaryLeader,
  clearStoredRoomAccessToken,
  createOrJoinScoutingRoom,
  getEventSchedule,
  getScoutingRoomState,
  getEventTeamsIntel,
  getStoredRoomAccessToken,
  getSuggestedEvents,
  getTeamIntel,
  kickScoutingRoomMember,
  removeScoutingRoomSecondaryLeader,
  saveScoutingRoomEntry,
  setStoredRoomAccessToken,
  scoutingRoomWebSocketUrl,
} from '../api';
import type {
  EventScheduleItem,
  ScoutingRoomAccess,
  ScoutingRoomAssignmentRecord,
  ScoutingRoomEntryRecord,
  ScoutingRoomMeta,
  ScoutingRoomPresenceMember,
} from '../api';
import { EventPicker } from '../components/EventPicker';
import { PageViewBar } from '../components/PageViewBar';
import { SCOUTING_VIEWS } from '../components/pageViewBarConfig';
import { AutoScoutEvidencePanel } from '../components/scouting/AutoScoutEvidencePanel';
import { AutoScoutFieldBadge } from '../components/scouting/AutoScoutFieldBadge';
import { AutoScoutReviewPanel } from '../components/scouting/AutoScoutReviewPanel';
import { SurfaceCard } from '../components/ui/SurfaceCard';
import { SegmentedTabs } from '../components/ui/SegmentedTabs';
import { useAutoScoutDraft } from '../hooks/useAutoScoutDraft';
import { useMobileLayout } from '../hooks/useMobileLayout';
import { usePageVisibility } from '../hooks/usePageVisibility';
import { readStoredCenterContext, writeCenterContext } from '../layout/centerContext';
import { countersFor, scalesFor } from '../config/gameFields';
import { downloadCsv } from '../utils/csvExport';
import { hapticTap, hapticUndo, hapticSuccess } from '../utils/haptics';
import {
  PlayIcon, PauseIcon, ResetIcon, SettingsIcon, ClipboardIcon,
  ClipboardCheckIcon, ZapIcon, CameraIcon, StarIcon, ClockIcon,
  RobotIcon, GamepadIcon, FlagIcon, MapPinIcon, PenIcon,
  SteeringWheelIcon, TargetIcon, LiveDotIcon, SaveIcon,
  WifiIcon, WifiOffIcon, CopyIcon, RefreshIcon, LogOutIcon,
  TrashIcon, DownloadIcon, QrCodeIcon,
  CalendarIcon, ScoreboardIcon,
  UsersIcon, SearchIcon,
  BarChartIcon, ChevronDownIcon, ChevronUpIcon,
} from '../components/ui/Icons';
import {
  asRecord,
  clampNumber,
  CURRENT_SEASON_YEAR,
  FALLBACK_SEASON_YEAR,
  fmtDateShort,
  isTransientAbortLikeError,
  liveTimerLabel,
  parseNumber,
  relativeFromTimestamp,
  teamNumberFromTeamKey,
  normalizeTeamKeyInput,
  titleizeKey,
} from './centerUtils';
import type {
  ApiTeamSnapshot,
  CounterField,
  EndgameMode,
  EventTeamApiBaseline,
  FloatingTimerPosition,
  Level1To5,
  MatchTeamOption,
  MobileCapturePanel,
  MobileHistoryPanel,
  MobileScorePanel,
  MobileScoutSection,
  RoomConnectionState,
  RpState,
  SavedScoutingEntry,
  ScoutFormState,
  ScoutingMode,
  TeamMatchPerformanceRow,
  TeamRollup,
  TeamSummaryRow,
} from './scoutingPage.types';
import {
  apiSnapshotFromIntel,
  buildSavedScoutingEntry,
  compLevelLabel,
  copyToClipboard,
  defaultFloatingTimerPosition,
  driverCompetency,
  EMPTY_FORM,
  EMPTY_RP,
  ENDGAME_LABELS,
  ENDGAME_POINTS,
  entriesFromRoomRecords,
  entryFileName,
  entryHeadUp,
  floatingTimerBounds,
  inferMatchCompleted,
  inferWinnerAlliance,
  manualScoutingRating,
  mergeEntries,
  mergeEntry,
  MOBILE_CAPTURE_PANEL_TABS,
  MOBILE_HISTORY_PANEL_TABS,
  MOBILE_SCORE_PANEL_TABS,
  normalizeEntry,
  normalizeEventKeyInput,
  normalizeRoomKey,
  normalizeScoutingNotes,
  normalizeScoutProfile,
  overallScoutRating,
  parseTeamOptions,
  phaseLabelForTimer,
  pointsFromForm,
  preferredMatchKey,
  readStoredEntries,
  readStoredFloatingTimerPosition,
  readStoredMobileCompactMode,
  scoutingApiRating,
  scoreTierLevel,
  stripEntriesForRoom,
  teamAllianceForMatch,
} from './scoutingPage.helpers';
import { QrShareModal, QrImportModal, RoomQrModal } from './QrShareModals';
import { getOrCreateScoutingRoomClientId } from './scoutingRoomClientId';

const SCOUTING_ENTRIES_STORAGE = 'scouting_manual_entries_v2';
const SCOUT_PROFILE_STORAGE = 'scouting_manual_profile_v1';
const SCOUTING_ROOM_KEY_STORAGE = 'scouting_room_active_key_v1';
const SCOUT_MY_TEAM_STORAGE = 'scouting_manual_my_team_v1';
const SCOUTING_TIMER_FLOAT_STORAGE = 'scouting_timer_float_pos_v1';
const SCOUTING_MOBILE_COMPACT_STORAGE = 'scouting_mobile_compact_mode_v1';
const SCOUTING_MOBILE_PANEL_PREFS_STORAGE = 'scouting_mobile_panel_prefs_v1';
const MATCH_DURATION_SEC = 150;
const ROOM_WS_HEARTBEAT_MS = 12000;
const ROOM_WS_MAX_RECONNECT_ATTEMPTS = 7;
const ROOM_HTTP_FALLBACK_POLL_MS = 4500;
const ROOM_ACCESS_REFRESH_LEEWAY_SEC = 90;
type SidebarSection = 'setup' | 'room' | 'data';
type EntryCaptureMode = 'manual' | 'auto';
const ROOM_WS_DISABLED = new Set(['1', 'true', 'yes', 'on']).has(
  String(
    import.meta.env.VITE_SCOUTING_WS_DISABLED || import.meta.env.NEXT_PUBLIC_SCOUTING_WS_DISABLED || '',
  )
    .trim()
    .toLowerCase(),
);

function roomAccessExpired(access: ScoutingRoomAccess | null | undefined): boolean {
  if (!access || !access.room_access_token) return true;
  const expiresAtUnix = Number(access.expires_at_unix || 0);
  if (!Number.isFinite(expiresAtUnix) || expiresAtUnix <= 0) return false;
  return Date.now() >= Math.floor(expiresAtUnix * 1000);
}

function isRoomAccessAuthError(detail: string): boolean {
  const lookup = String(detail || '').trim().toLowerCase();
  if (!lookup) return false;
  return (
    lookup.includes('x-room-access-token')
    || lookup.includes('room access token')
    || lookup.includes('authorization failed')
    || lookup.includes('requires a valid')
  );
}

function roomRequestErrorMessage(detail: string, action: string): string {
  if (isTransientAbortLikeError(detail)) {
    return `${action} timed out before the server replied. Check the API/backend and retry.`;
  }
  return detail;
}

type StoredMobilePanelPrefs = {
  finderOpen: boolean;
  section: MobileScoutSection;
  capture: MobileCapturePanel;
  score: MobileScorePanel;
  history: MobileHistoryPanel;
};

const DEFAULT_MOBILE_PANEL_PREFS: StoredMobilePanelPrefs = {
  finderOpen: true,
  section: 'capture',
  capture: 'teleop',
  score: 'live',
  history: 'team_matches',
};

function readStoredMobilePanelPrefs(): StoredMobilePanelPrefs {
  try {
    const raw = window.localStorage.getItem(SCOUTING_MOBILE_PANEL_PREFS_STORAGE);
    if (!raw) return DEFAULT_MOBILE_PANEL_PREFS;
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    const finderOpen = typeof parsed.finderOpen === 'boolean'
      ? parsed.finderOpen
      : DEFAULT_MOBILE_PANEL_PREFS.finderOpen;
    const section = parsed.section === 'capture' || parsed.section === 'score' || parsed.section === 'history'
      ? parsed.section
      : DEFAULT_MOBILE_PANEL_PREFS.section;
    const capture = MOBILE_CAPTURE_PANEL_TABS.some((panel) => panel.id === parsed.capture)
      ? (parsed.capture as MobileCapturePanel)
      : DEFAULT_MOBILE_PANEL_PREFS.capture;
    const score = MOBILE_SCORE_PANEL_TABS.some((panel) => panel.id === parsed.score)
      ? (parsed.score as MobileScorePanel)
      : DEFAULT_MOBILE_PANEL_PREFS.score;
    const history = MOBILE_HISTORY_PANEL_TABS.some((panel) => panel.id === parsed.history)
      ? (parsed.history as MobileHistoryPanel)
      : DEFAULT_MOBILE_PANEL_PREFS.history;
    return {
      finderOpen,
      section,
      capture,
      score,
      history,
    };
  } catch {
    return DEFAULT_MOBILE_PANEL_PREFS;
  }
}



function CounterInput(props: {
  label: string;
  value: number;
  onChange: (next: number) => void;
  min?: number;
  max?: number;
  step?: number;
  badge?: ReactNode;
}) {
  const { label, value, onChange, min = 0, max = 999, step = 1, badge = null } = props;
  function handleDirectInput(rawValue: string) {
    if (rawValue.trim() === '') {
      onChange(min);
      return;
    }
    const parsed = Number.parseInt(rawValue, 10);
    if (Number.isNaN(parsed)) return;
    onChange(clampNumber(parsed, min, max));
  }

  return (
    <div className="scout-stepper">
      <div className="scout-stepper-label">
        <span>{label}</span>
        {badge}
      </div>
      <div className="scout-stepper-controls">
        <button
          type="button"
          className="center-btn ghost scout-stepper-btn"
          onClick={() => {
            hapticUndo();
            onChange(clampNumber(value - step, min, max));
          }}
          aria-label={`Decrease ${label}`}
        >
          -
        </button>
        <input
          className="scout-stepper-input"
          type="number"
          inputMode="numeric"
          min={min}
          max={max}
          step={step}
          value={value}
          onChange={(event) => handleDirectInput(event.target.value)}
          aria-label={`${label} value`}
        />
        <button
          type="button"
          className="center-btn scout-stepper-btn"
          onClick={() => {
            hapticTap();
            onChange(clampNumber(value + step, min, max));
          }}
          aria-label={`Increase ${label}`}
        >
          +
        </button>
      </div>
    </div>
  );
}

function ScaleInput(props: {
  label: string;
  value: Level1To5;
  onChange: (next: Level1To5) => void;
  badge?: ReactNode;
}) {
  const { label, value, onChange, badge = null } = props;
  return (
    <div className="scout-scale-input">
      <span className="scout-scale-label">
        <span>{label}</span>
        {badge}
      </span>
      <div className="scout-scale-row">
        {[1, 2, 3, 4, 5].map((level) => (
          <button
            type="button"
            key={`${label}-${level}`}
            className={`scout-scale-btn ${value === level ? 'active' : ''}`.trim()}
            onClick={() => {
              hapticTap();
              onChange(level as Level1To5);
            }}
          >
            {level}
          </button>
        ))}
        <button
          type="button"
          className={`scout-scale-btn scout-scale-btn-na ${value === null ? 'active' : ''}`.trim()}
          onClick={() => onChange(null)}
        >
          N/A
        </button>
      </div>
    </div>
  );
}

export function ScoutingPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();
  const isMobileLayout = useMobileLayout();
  const pageVisible = usePageVisibility();
  const storedCenterContext = readStoredCenterContext();

  const defaultEventKey = normalizeEventKeyInput(
    searchParams.get('event') || storedCenterContext.eventKey || '',
  );
  const defaultMatchKey = normalizeEventKeyInput(searchParams.get('match') || storedCenterContext.matchKey || '');
  const defaultTeamKey = (searchParams.get('team') || storedCenterContext.teamKey || '').trim().toLowerCase();
  const defaultMyTeamKey = (searchParams.get('my_team') || window.localStorage.getItem(SCOUT_MY_TEAM_STORAGE) || '')
    .trim()
    .toLowerCase();
  const mobilePanelPrefs = useMemo(() => readStoredMobilePanelPrefs(), []);

  const [scoutingMode, setScoutingMode] = useState<ScoutingMode>('match');
  const [entryCaptureMode, setEntryCaptureMode] = useState<EntryCaptureMode>('manual');
  const [eventInput, setEventInput] = useState(defaultEventKey);
  const [selectedEventKey, setSelectedEventKey] = useState(defaultEventKey);
  const [eventFetchCtr, setEventFetchCtr] = useState(0);
  const [scheduleRows, setScheduleRows] = useState<EventScheduleItem[]>([]);
  const [scheduleName, setScheduleName] = useState<string | null>(null);
  const [selectedMatchKey, setSelectedMatchKey] = useState(defaultMatchKey);
  const [selectedTeamKey, setSelectedTeamKey] = useState(defaultTeamKey);
  const [myTeamKey, setMyTeamKey] = useState(defaultMyTeamKey);
  const [form, setForm] = useState<ScoutFormState>(EMPTY_FORM);
  const [rpState, setRpState] = useState<RpState>(EMPTY_RP);
  const [scoutNotes, setScoutNotes] = useState('');
  const [entries, setEntries] = useState<SavedScoutingEntry[]>(() => readStoredEntries());
  const [timerSec, setTimerSec] = useState(MATCH_DURATION_SEC);
  const [timerRunning, setTimerRunning] = useState(false);
  const [floatingTimerPosition, setFloatingTimerPosition] = useState<FloatingTimerPosition>(() => {
    const stored = readStoredFloatingTimerPosition();
    if (stored) return stored;
    if (typeof window !== 'undefined') return defaultFloatingTimerPosition(window.innerWidth);
    return { x: 10, y: 74 };
  });
  const [floatingTimerDragging, setFloatingTimerDragging] = useState(false);
  const [loadingSuggested, setLoadingSuggested] = useState(false);
  const [loadingSchedule, setLoadingSchedule] = useState(false);
  const [loadingEventIntel, setLoadingEventIntel] = useState(false);
  const [savingEntry, setSavingEntry] = useState(false);
  const [statusText, setStatusText] = useState('Select event, match, and team.');
  const [errorText, setErrorText] = useState('');
  const [eventIntelError, setEventIntelError] = useState('');
  const [lastSavedAt, setLastSavedAt] = useState<number | null>(null);
  const [lastSavedEntryId, setLastSavedEntryId] = useState<string>('');
  const [qrShareEntry, setQrShareEntry] = useState<SavedScoutingEntry | null>(null);
  const [qrImportOpen, setQrImportOpen] = useState(false);
  const [roomQrOpen, setRoomQrOpen] = useState(false);
  const [historyOnlyCurrentEvent, setHistoryOnlyCurrentEvent] = useState(true);
  const [historyMineOnly, setHistoryMineOnly] = useState(true);
  const [quickTeamQuery, setQuickTeamQuery] = useState('');
  const [scoutProfile, setScoutProfile] = useState(() =>
    normalizeScoutProfile(window.localStorage.getItem(SCOUT_PROFILE_STORAGE) || ''),
  );
  const [roomKeyInput, setRoomKeyInput] = useState(() =>
    normalizeRoomKey(window.sessionStorage.getItem(SCOUTING_ROOM_KEY_STORAGE) || ''),
  );
  const [activeRoom, setActiveRoom] = useState<ScoutingRoomMeta | null>(null);
  const [activeRoomAccess, setActiveRoomAccess] = useState<ScoutingRoomAccess | null>(null);
  const [roomPresence, setRoomPresence] = useState<ScoutingRoomPresenceMember[]>([]);
  const [roomAssignments, setRoomAssignments] = useState<ScoutingRoomAssignmentRecord[]>([]);
  const [roomKickPendingProfile, setRoomKickPendingProfile] = useState('');
  const [roomPromotePendingProfile, setRoomPromotePendingProfile] = useState('');
  const [roomDemotePendingProfile, setRoomDemotePendingProfile] = useState('');
  const [roomConnectionState, setRoomConnectionState] = useState<RoomConnectionState>('disconnected');
  const [roomErrorText, setRoomErrorText] = useState('');
  const [roomHttpFallbackActive, setRoomHttpFallbackActive] = useState(false);
  const [roomClientId] = useState(() => getOrCreateScoutingRoomClientId());
  const [roomSocketNonce, setRoomSocketNonce] = useState(0);
  const [eventApiBaselineByTeam, setEventApiBaselineByTeam] = useState<Record<string, EventTeamApiBaseline>>({});
  const [apiSnapshotCache, setApiSnapshotCache] = useState<Record<string, ApiTeamSnapshot | null>>({});
  const [showTeamSummaries, setShowTeamSummaries] = useState(false);
  const [sidebarSection, setSidebarSection] = useState<SidebarSection>('setup');
  const [mobileFinderOpen, setMobileFinderOpen] = useState<boolean>(() => mobilePanelPrefs.finderOpen);
  const [mobileScoutSection, setMobileScoutSection] = useState<MobileScoutSection>(() => mobilePanelPrefs.section);
  const [mobileCapturePanel, setMobileCapturePanel] = useState<MobileCapturePanel>(() => mobilePanelPrefs.capture);
  const [mobileScorePanel, setMobileScorePanel] = useState<MobileScorePanel>(() => mobilePanelPrefs.score);
  const [mobileHistoryPanel] = useState<MobileHistoryPanel>(() => mobilePanelPrefs.history);
  const [mobileCompactMode, setMobileCompactMode] = useState<boolean>(() => readStoredMobileCompactMode());
  const [scoutingTopCondensed, setScoutingTopCondensed] = useState(false);
  const [scoutingTopHidden, setScoutingTopHidden] = useState(false);
  const [setupHeaderManuallyCollapsed, setSetupHeaderManuallyCollapsed] = useState(false);
  const setupSectionRef = useRef<HTMLDivElement | null>(null);
  const roomSectionRef = useRef<HTMLDivElement | null>(null);
  const dataSectionRef = useRef<HTMLDivElement | null>(null);
  const restoredRoomRef = useRef(false);
  const roomReconnectAttemptsRef = useRef(0);
  const roomReconnectTimerRef = useRef<number | null>(null);
  const roomAccessRefreshPromiseRef = useRef<Promise<string> | null>(null);
  const floatingTimerRef = useRef<HTMLDivElement | null>(null);
  const floatingTimerDragRef = useRef<{
    mode: 'pointer' | 'touch';
    pointerId: number | null;
    touchId: number | null;
    startClientX: number;
    startClientY: number;
    startX: number;
    startY: number;
  } | null>(null);

  const applyRoomAccess = useCallback((roomKey: string, access: ScoutingRoomAccess | null): void => {
    const normalizedRoomKey = normalizeRoomKey(roomKey);
    if (!normalizedRoomKey) {
      setActiveRoomAccess(null);
      return;
    }
    if (!access || !access.room_access_token) {
      clearStoredRoomAccessToken(normalizedRoomKey);
      setActiveRoomAccess(null);
      return;
    }
    setStoredRoomAccessToken(normalizedRoomKey, access.room_access_token, access.expires_at_unix);
    setActiveRoomAccess(access);
  }, []);

  const resolveRoomAccessToken = useCallback((roomKey: string): string => {
    const normalizedRoomKey = normalizeRoomKey(roomKey);
    if (!normalizedRoomKey) return '';
    if (
      activeRoomAccess &&
      activeRoomAccess.room_access_token &&
      normalizeRoomKey(activeRoom?.room_key || '') === normalizedRoomKey &&
      !roomAccessExpired(activeRoomAccess)
    ) {
      return activeRoomAccess.room_access_token;
    }
    return getStoredRoomAccessToken(normalizedRoomKey);
  }, [activeRoom?.room_key, activeRoomAccess]);

  const refreshRoomAccessSession = useCallback(async (roomKey: string, options?: { silent?: boolean }): Promise<string> => {
    const normalizedRoomKey = normalizeRoomKey(roomKey);
    const normalizedScoutProfile = normalizeScoutProfile(scoutProfile);
    if (!normalizedRoomKey || !normalizedScoutProfile) return '';
    const inFlight = roomAccessRefreshPromiseRef.current;
    if (inFlight) return inFlight;
    const run = (async () => {
      try {
        const response = await createOrJoinScoutingRoom({
          room_key: normalizedRoomKey,
          event_key: selectedEventKey || activeRoom?.event_key || undefined,
          scout_profile: normalizedScoutProfile,
          client_id: roomClientId,
          title: selectedEventKey ? `Scouting ${selectedEventKey}` : undefined,
          create_if_missing: false,
          room_access_token: resolveRoomAccessToken(normalizedRoomKey) || undefined,
          timeoutMs: 25000,
        });
        const room = response.room;
        const joinedRoomKey = normalizeRoomKey(room.room_key) || normalizedRoomKey;
        setActiveRoom(room);
        applyRoomAccess(joinedRoomKey, response.access || null);
        setRoomPresence(Array.isArray(room.presence) ? room.presence : []);
        setRoomAssignments(Array.isArray(response.assignments) ? response.assignments : []);
        const roomEntries = entriesFromRoomRecords(response.entries || [], joinedRoomKey);
        setEntries((current) => {
          const withoutRoomEntries = stripEntriesForRoom(current, joinedRoomKey);
          if (roomEntries.length === 0) return withoutRoomEntries;
          return mergeEntries(withoutRoomEntries, roomEntries);
        });
        setRoomErrorText('');
        if (!options?.silent) {
          setStatusText(`Room session refreshed for ${joinedRoomKey}.`);
        }
        return (
          String(response.access?.room_access_token || '').trim()
          || resolveRoomAccessToken(joinedRoomKey)
        );
      } catch (error) {
        if (!options?.silent) {
          const detail = (error as Error).message || 'Unable to refresh room access.';
          setRoomErrorText(roomRequestErrorMessage(detail, 'Refreshing the room session'));
        }
        return '';
      } finally {
        roomAccessRefreshPromiseRef.current = null;
      }
    })();
    roomAccessRefreshPromiseRef.current = run;
    return run;
  }, [
    activeRoom?.event_key,
    applyRoomAccess,
    resolveRoomAccessToken,
    roomClientId,
    scoutProfile,
    selectedEventKey,
  ]);

  const ensureRoomAccessToken = useCallback(async (roomKey: string): Promise<string> => {
    const resolved = resolveRoomAccessToken(roomKey);
    if (resolved) return resolved;
    return refreshRoomAccessSession(roomKey, { silent: true });
  }, [refreshRoomAccessSession, resolveRoomAccessToken]);

  const selectedMatch = useMemo(
    () => scheduleRows.find((row) => row.match_key.toLowerCase() === selectedMatchKey.toLowerCase()) || null,
    [scheduleRows, selectedMatchKey],
  );

  const teamOptions = useMemo(() => parseTeamOptions(selectedMatch), [selectedMatch]);
  const selectedTeam = useMemo(
    () => teamOptions.find((option) => option.team_key === selectedTeamKey) || null,
    [selectedTeamKey, teamOptions],
  );
  const autoScout = useAutoScoutDraft({
    enabled: entryCaptureMode === 'auto',
    eventKey: selectedEventKey,
    matchKey: selectedMatchKey,
    teamKey: selectedTeamKey,
    scoutProfile,
    form,
    notes: scoutNotes,
    setForm,
    setNotes: setScoutNotes,
  });

  const eventTeamOptions = useMemo(() => {
    const map = new Map<string, string>();
    for (const match of scheduleRows) {
      for (const team of parseTeamOptions(match)) {
        if (!map.has(team.team_key)) {
          map.set(team.team_key, team.team_label || team.team_key.toUpperCase());
        }
      }
    }
    for (const key of Object.keys(eventApiBaselineByTeam)) {
      const normalized = key.trim().toLowerCase();
      if (!normalized || map.has(normalized)) continue;
      const teamNumber = teamNumberFromTeamKey(normalized);
      map.set(normalized, teamNumber !== null ? `#${teamNumber}` : normalized.toUpperCase());
    }
    return Array.from(map.entries())
      .map(([team_key, team_label]) => ({ team_key, team_label }))
      .sort((a, b) => {
        const aNum = teamNumberFromTeamKey(a.team_key) ?? Number.MAX_SAFE_INTEGER;
        const bNum = teamNumberFromTeamKey(b.team_key) ?? Number.MAX_SAFE_INTEGER;
        if (aNum !== bNum) return aNum - bNum;
        return a.team_key.localeCompare(b.team_key);
      });
  }, [eventApiBaselineByTeam, scheduleRows]);

  const upcomingScheduleTargets = useMemo(() => {
    const nowMs = Date.now();
    return scheduleRows
      .filter((match) => !inferMatchCompleted(match, nowMs))
      .map((match) => ({
        match_key: match.match_key.toLowerCase(),
        match_display: match.display_name,
        scheduled_time: match.scheduled_time,
        teams: parseTeamOptions(match),
      }))
      .sort((a, b) => {
        const aTime = a.scheduled_time || Number.MAX_SAFE_INTEGER;
        const bTime = b.scheduled_time || Number.MAX_SAFE_INTEGER;
        if (aTime !== bTime) return aTime - bTime;
        return a.match_key.localeCompare(b.match_key);
      });
  }, [scheduleRows]);

  const nextMatchTarget = useMemo(() => {
    if (upcomingScheduleTargets.length === 0) return null;
    const currentMatchKey = selectedMatchKey.trim().toLowerCase();
    const currentIndex = upcomingScheduleTargets.findIndex((row) => row.match_key === currentMatchKey);
    if (currentIndex >= 0 && currentIndex < upcomingScheduleTargets.length - 1) {
      return upcomingScheduleTargets[currentIndex + 1];
    }
    return upcomingScheduleTargets[0];
  }, [selectedMatchKey, upcomingScheduleTargets]);

  const nextTeamTarget = useMemo(() => {
    const normalizedSelectedTeam = selectedTeamKey.trim().toLowerCase();
    if (teamOptions.length > 0) {
      const currentIndex = teamOptions.findIndex((team) => team.team_key === normalizedSelectedTeam);
      if (currentIndex >= 0 && currentIndex < teamOptions.length - 1) {
        return {
          match_key: selectedMatchKey.trim().toLowerCase(),
          match_display: selectedMatch?.display_name || selectedMatchKey.toUpperCase(),
          team_key: teamOptions[currentIndex + 1]?.team_key || '',
          team_label: teamOptions[currentIndex + 1]?.team_label || teamOptions[currentIndex + 1]?.team_key?.toUpperCase() || '',
        };
      }
    }
    if (nextMatchTarget && nextMatchTarget.teams.length > 0) {
      const candidateTeam = nextMatchTarget.teams[0];
      return {
        match_key: nextMatchTarget.match_key,
        match_display: nextMatchTarget.match_display,
        team_key: candidateTeam.team_key,
        team_label: candidateTeam.team_label || candidateTeam.team_key.toUpperCase(),
      };
    }
    if (eventTeamOptions.length > 0) {
      const currentIndex = eventTeamOptions.findIndex((team) => team.team_key === normalizedSelectedTeam);
      const fallback = currentIndex >= 0 && currentIndex < eventTeamOptions.length - 1
        ? eventTeamOptions[currentIndex + 1]
        : eventTeamOptions[0];
      if (!fallback) return null;
      return {
        match_key: selectedMatchKey.trim().toLowerCase(),
        match_display: selectedMatch?.display_name || selectedMatchKey.toUpperCase(),
        team_key: fallback.team_key,
        team_label: fallback.team_label || fallback.team_key.toUpperCase(),
      };
    }
    return null;
  }, [eventTeamOptions, nextMatchTarget, selectedMatch?.display_name, selectedMatchKey, selectedTeamKey, teamOptions]);

  const upcomingMatchTargets = useMemo(() => {
    if (!myTeamKey) return [] as Array<{
      match_key: string;
      match_display: string;
      scheduled_time: number | null;
      status: 'upcoming' | 'live' | 'unknown';
      opponents: MatchTeamOption[];
    }>;
    const normalizedMyTeam = myTeamKey.trim().toLowerCase();
    if (!normalizedMyTeam) return [];
    const nowMs = Date.now();
    return scheduleRows
      .map((match) => {
        const alliance = teamAllianceForMatch(match, normalizedMyTeam);
        if (!alliance) return null;
        if (inferMatchCompleted(match, nowMs)) return null;
        const timer = liveTimerLabel(match.scheduled_time, nowMs);
        const opponents = parseTeamOptions(match).filter((team) => team.team_key !== normalizedMyTeam);
        return {
          match_key: match.match_key.toLowerCase(),
          match_display: match.display_name,
          scheduled_time: match.scheduled_time,
          status: timer.state === 'live' ? 'live' : timer.state === 'upcoming' ? 'upcoming' : 'unknown',
          opponents,
        };
      })
      .filter((row): row is {
        match_key: string;
        match_display: string;
        scheduled_time: number | null;
        status: 'upcoming' | 'live' | 'unknown';
        opponents: MatchTeamOption[];
      } => Boolean(row))
      .sort((a, b) => {
        const aTime = a.scheduled_time || Number.MAX_SAFE_INTEGER;
        const bTime = b.scheduled_time || Number.MAX_SAFE_INTEGER;
        if (aTime !== bTime) return aTime - bTime;
        return a.match_key.localeCompare(b.match_key);
      })
      .slice(0, 20);
  }, [myTeamKey, scheduleRows]);

  const upcomingOpponentOptions = useMemo(() => {
    const nextMap = new Map<string, {
      team_key: string;
      team_label: string;
      next_match_key: string;
      next_match_display: string;
      next_scheduled_time: number | null;
      status: 'upcoming' | 'live' | 'unknown';
      appearances: number;
    }>();
    for (const upcoming of upcomingMatchTargets) {
      for (const opponent of upcoming.opponents) {
        const current = nextMap.get(opponent.team_key);
        if (!current) {
          nextMap.set(opponent.team_key, {
            team_key: opponent.team_key,
            team_label: opponent.team_label,
            next_match_key: upcoming.match_key,
            next_match_display: upcoming.match_display,
            next_scheduled_time: upcoming.scheduled_time,
            status: upcoming.status,
            appearances: 1,
          });
          continue;
        }
        current.appearances += 1;
        const currentTime = current.next_scheduled_time || Number.MAX_SAFE_INTEGER;
        const nextTime = upcoming.scheduled_time || Number.MAX_SAFE_INTEGER;
        if (nextTime < currentTime) {
          current.next_match_key = upcoming.match_key;
          current.next_match_display = upcoming.match_display;
          current.next_scheduled_time = upcoming.scheduled_time;
          current.status = upcoming.status;
        }
      }
    }
    return Array.from(nextMap.values()).sort((a, b) => {
      const aTime = a.next_scheduled_time || Number.MAX_SAFE_INTEGER;
      const bTime = b.next_scheduled_time || Number.MAX_SAFE_INTEGER;
      if (aTime !== bTime) return aTime - bTime;
      return a.team_key.localeCompare(b.team_key);
    });
  }, [upcomingMatchTargets]);

  const pointsSummary = useMemo(() => pointsFromForm(form), [form]);
  const hasScoutProfile = scoutProfile.trim().length > 0;

  const rpSuggested = useMemo(
    () => ({
      energized: pointsSummary.total >= 34,
      supercharged: pointsSummary.total >= 120,
      traversal: pointsSummary.endgame >= 17,
    }),
    [pointsSummary.endgame, pointsSummary.total],
  );

  const driverScore = useMemo(() => driverCompetency(form), [form]);
  const liveManualRating = useMemo(() => manualScoutingRating(form, driverScore), [form, driverScore]);
  const liveOverallScoutRating = useMemo(
    () => overallScoutRating(pointsSummary, liveManualRating, driverScore),
    [driverScore, liveManualRating, pointsSummary],
  );
  const liveRobotRankLevel = useMemo(
    () => scoreTierLevel(liveOverallScoutRating.score_0_100),
    [liveOverallScoutRating.score_0_100],
  );
  const timerPhase = phaseLabelForTimer(timerSec);
  const timerClockLabel = `${String(Math.floor(timerSec / 60)).padStart(2, '0')}:${String(timerSec % 60).padStart(2, '0')}`;
  const timerPhaseState: 'live' | 'upcoming' | 'ended' | 'unknown' = timerPhase === 'Auto'
    ? 'upcoming'
    : timerPhase === 'Teleop'
      ? 'live'
      : timerPhase === 'Endgame'
        ? 'ended'
        : 'unknown';
  const liveTimer = useMemo(
    () => liveTimerLabel(selectedMatch?.scheduled_time || null, Date.now()),
    [selectedMatch?.scheduled_time],
  );
  const showCaptureSection = !isMobileLayout || mobileScoutSection === 'capture';
  const showScoreSection = !isMobileLayout || mobileScoutSection === 'score';
  const showHistorySection = !isMobileLayout;
  const showCaptureAutoCard = !isMobileLayout || mobileCapturePanel === 'auto';
  const showCaptureTeleopCard = !isMobileLayout || mobileCapturePanel === 'teleop';
  const showCaptureEndgameCard = !isMobileLayout || mobileCapturePanel === 'endgame';
  const showCaptureMobilityCard = !isMobileLayout || mobileCapturePanel === 'mobility';
  const showDesktopStackedAutoEndgame = !isMobileLayout && showCaptureAutoCard && showCaptureEndgameCard;
  const showCaptureStrategyCards = !isMobileLayout || mobileCapturePanel === 'strategy';
  const showCaptureNotesCard = !isMobileLayout || mobileCapturePanel === 'notes';
  const showScoreDriverCard = !isMobileLayout;
  const showScorePointsCard = !isMobileLayout || mobileScorePanel === 'points';
  const showScoreLiveCard = !isMobileLayout || mobileScorePanel === 'live';
  const showScoreSavedCard = !isMobileLayout;
  const showHistoryTeamMatchesCard = !isMobileLayout || mobileHistoryPanel === 'team_matches';
  const showHistoryTeamRollupsCard = !isMobileLayout || mobileHistoryPanel === 'team_rollups';
  const showHistoryEntriesCard = !isMobileLayout || mobileHistoryPanel === 'entries';
  const showHistorySummariesCard = !isMobileLayout ? showTeamSummaries : mobileHistoryPanel === 'summaries';
  const supportsPointerEvents = typeof window !== 'undefined' && typeof window.PointerEvent !== 'undefined';
  const floatingTimerStyle = useMemo(
    () => ({
      left: `${Math.round(floatingTimerPosition.x)}px`,
      top: `${Math.round(floatingTimerPosition.y)}px`,
    }),
    [floatingTimerPosition.x, floatingTimerPosition.y],
  );

  function clampFloatingTimerToViewport(next: FloatingTimerPosition): FloatingTimerPosition {
    if (typeof window === 'undefined') return next;
    const bounds = floatingTimerBounds();
    return {
      x: clampNumber(next.x, bounds.minX, bounds.maxX),
      y: clampNumber(next.y, bounds.minY, bounds.maxY),
    };
  }

  function beginFloatingTimerDrag(
    startClientX: number,
    startClientY: number,
    options: { mode: 'pointer'; pointerId: number } | { mode: 'touch'; touchId: number },
  ) {
    const start = clampFloatingTimerToViewport(floatingTimerPosition);
    floatingTimerDragRef.current = {
      mode: options.mode,
      pointerId: options.mode === 'pointer' ? options.pointerId : null,
      touchId: options.mode === 'touch' ? options.touchId : null,
      startClientX,
      startClientY,
      startX: start.x,
      startY: start.y,
    };
    setFloatingTimerDragging(true);
    setFloatingTimerPosition(start);
  }

  function handleFloatingTimerDragStart(event: React.PointerEvent<HTMLDivElement>) {
    if (event.pointerType === 'mouse' && event.button !== 0) return;
    beginFloatingTimerDrag(event.clientX, event.clientY, { mode: 'pointer', pointerId: event.pointerId });
    event.currentTarget.setPointerCapture?.(event.pointerId);
    event.preventDefault();
  }

  function handleFloatingTimerTouchStart(event: React.TouchEvent<HTMLDivElement>) {
    // Modern mobile browsers emit pointer events; avoid dual-start conflicts.
    if (supportsPointerEvents) return;
    const touch = event.changedTouches[0];
    if (!touch) return;
    beginFloatingTimerDrag(touch.clientX, touch.clientY, { mode: 'touch', touchId: touch.identifier });
    event.preventDefault();
  }

  const roomLeaderProfile = normalizeScoutProfile(
    String(activeRoom?.leader_scout_profile || activeRoom?.created_by || ''),
  );
  const roomCreatorProfile = normalizeScoutProfile(String(activeRoom?.created_by || ''));
  const secondaryLeaderProfiles = useMemo(() => {
    const rawProfiles = Array.isArray(activeRoom?.secondary_leader_scout_profiles)
      ? activeRoom.secondary_leader_scout_profiles
      : [];
    const creatorLookup = roomCreatorProfile.toLowerCase();
    const next: string[] = [];
    const seen = new Set<string>();
    for (const raw of rawProfiles) {
      const normalized = normalizeScoutProfile(String(raw || ''));
      const lookup = normalized.toLowerCase();
      if (!lookup || lookup === creatorLookup || seen.has(lookup)) continue;
      seen.add(lookup);
      next.push(normalized);
    }
    return next.sort((a, b) => a.localeCompare(b));
  }, [activeRoom?.secondary_leader_scout_profiles, roomCreatorProfile]);
  const roomOwnerLookups = useMemo(() => {
    const lookups = new Set<string>();
    const creatorLookup = roomCreatorProfile.toLowerCase();
    if (creatorLookup) lookups.add(creatorLookup);
    for (const profile of secondaryLeaderProfiles) {
      const lookup = normalizeScoutProfile(profile).toLowerCase();
      if (lookup) lookups.add(lookup);
    }
    return lookups;
  }, [roomCreatorProfile, secondaryLeaderProfiles]);
  const hasRoomOwnerAuthority = useMemo(() => {
    const scoutLookup = normalizeScoutProfile(scoutProfile).toLowerCase();
    if (!scoutLookup) return false;
    const explicitRole = String(activeRoomAccess?.room_role || activeRoom?.room_role || '').trim().toLowerCase();
    if (explicitRole === 'owner') return true;
    if (roomOwnerLookups.has(scoutLookup)) return true;
    const activeLeaderLookup = normalizeScoutProfile(roomLeaderProfile).toLowerCase();
    return Boolean(activeLeaderLookup) && activeLeaderLookup === scoutLookup;
  }, [activeRoom?.room_role, activeRoomAccess?.room_role, roomLeaderProfile, roomOwnerLookups, scoutProfile]);
  const promotableRoomMembers = useMemo(() => {
    if (!hasRoomOwnerAuthority) return [] as Array<{ scout_profile: string; connections: number }>;
    const myLookup = normalizeScoutProfile(scoutProfile).toLowerCase();
    const byLookup = new Map<string, { scout_profile: string; connections: number }>();
    for (const member of roomPresence) {
      const profile = normalizeScoutProfile(member.scout_profile);
      const lookup = profile.toLowerCase();
      if (!lookup || lookup === myLookup || roomOwnerLookups.has(lookup)) continue;
      const connections = Math.max(0, Number(member.connections || 0));
      const existing = byLookup.get(lookup);
      if (!existing || connections > existing.connections) {
        byLookup.set(lookup, { scout_profile: profile, connections });
      }
    }
    return Array.from(byLookup.values()).sort((a, b) => a.scout_profile.localeCompare(b.scout_profile));
  }, [hasRoomOwnerAuthority, roomOwnerLookups, roomPresence, scoutProfile]);

  const kickableRoomMembers = useMemo(() => {
    if (!hasRoomOwnerAuthority) return [] as Array<{ scout_profile: string; connections: number }>;
    const myLookup = normalizeScoutProfile(scoutProfile).toLowerCase();
    return roomPresence
      .map((member) => ({
        scout_profile: normalizeScoutProfile(member.scout_profile),
        connections: Math.max(0, Number(member.connections || 0)),
      }))
      .filter((member) => {
        const lookup = member.scout_profile.toLowerCase();
        return Boolean(lookup) && lookup !== myLookup;
      })
      .sort((a, b) => a.scout_profile.localeCompare(b.scout_profile));
  }, [hasRoomOwnerAuthority, roomPresence, scoutProfile]);
  const demotableSecondaryLeaders = useMemo(() => {
    if (!hasRoomOwnerAuthority) return [] as string[];
    const myLookup = normalizeScoutProfile(scoutProfile).toLowerCase();
    return secondaryLeaderProfiles
      .filter((profile) => normalizeScoutProfile(profile).toLowerCase() !== myLookup)
      .sort((a, b) => a.localeCompare(b));
  }, [hasRoomOwnerAuthority, scoutProfile, secondaryLeaderProfiles]);

  const myRoomAssignments = useMemo(() => {
    const myLookup = normalizeScoutProfile(scoutProfile).toLowerCase();
    if (!myLookup) return [] as Array<{
      assignment_id: number;
      match_key: string;
      team_key: string;
      match_display: string;
      team_display: string;
      match_order: number;
    }>;
    const scheduleIndexByMatch = new Map<string, number>();
    scheduleRows.forEach((row, index) => {
      const key = String(row.match_key || '').trim().toLowerCase();
      if (!key || scheduleIndexByMatch.has(key)) return;
      scheduleIndexByMatch.set(key, index);
    });
    const scoped = roomAssignments.filter((row) => {
      const assignedLookup = normalizeScoutProfile(String(row.assigned_scout_profile || '')).toLowerCase();
      if (!assignedLookup || assignedLookup !== myLookup) return false;
      if (selectedEventKey && row.event_key && String(row.event_key).toLowerCase() !== selectedEventKey.toLowerCase()) {
        return false;
      }
      return true;
    });
    return scoped
      .map((row) => {
        const matchKey = String(row.match_key || '').trim().toLowerCase();
        const teamKey = String(row.team_key || '').trim().toLowerCase();
        const scheduleRow = scheduleRows.find((item) => item.match_key.toLowerCase() === matchKey) || null;
        const matchDisplay = scheduleRow?.display_name || matchKey.toUpperCase();
        const teamNumber = teamNumberFromTeamKey(teamKey);
        return {
          assignment_id: Number(row.id || 0),
          match_key: matchKey,
          team_key: teamKey,
          match_display: matchDisplay,
          team_display: teamNumber !== null ? `#${teamNumber}` : teamKey.toUpperCase(),
          match_order: scheduleIndexByMatch.get(matchKey) ?? Number.MAX_SAFE_INTEGER,
        };
      })
      .sort((a, b) => {
        if (a.match_order !== b.match_order) return a.match_order - b.match_order;
        if (a.match_key !== b.match_key) return a.match_key.localeCompare(b.match_key);
        return a.team_key.localeCompare(b.team_key);
      });
  }, [roomAssignments, scheduleRows, scoutProfile, selectedEventKey]);

  const scopedEntries = useMemo(() => {
    const activeRoomKey = normalizeRoomKey(activeRoom?.room_key || '');
    let filtered: SavedScoutingEntry[];
    if (historyMineOnly) {
      // Only my entries regardless of room
      filtered = entries.filter((entry) => normalizeScoutProfile(entry.scout_profile) === scoutProfile);
    } else if (activeRoomKey) {
      // Show my entries + entries that belong to the active room (from any scout)
      filtered = entries.filter((entry) => {
        const isMyEntry = normalizeScoutProfile(entry.scout_profile) === scoutProfile;
        const isRoomEntry = normalizeRoomKey(entry.room_key || '') === activeRoomKey;
        return isMyEntry || isRoomEntry;
      });
    } else {
      // No room active — only show my entries (no team context to share)
      filtered = entries.filter((entry) => normalizeScoutProfile(entry.scout_profile) === scoutProfile);
    }
    const byEvent = historyOnlyCurrentEvent && selectedEventKey
      ? filtered.filter((entry) => entry.event_key === selectedEventKey)
      : filtered;
    return byEvent;
  }, [entries, historyMineOnly, historyOnlyCurrentEvent, scoutProfile, selectedEventKey, activeRoom?.room_key]);

  const visibleEntries = useMemo(() => scopedEntries.slice(0, 100), [scopedEntries]);

  const selectedTeamMatchPerformance = useMemo(() => {
    if (!selectedTeamKey || !selectedEventKey) return [] as TeamMatchPerformanceRow[];
    const normalizedTeamKey = selectedTeamKey.trim().toLowerCase();
    if (!normalizedTeamKey) return [];
    const byMatch = new Map<string, SavedScoutingEntry[]>();
    for (const entry of entries) {
      if (entry.event_key !== selectedEventKey || entry.team_key !== normalizedTeamKey) continue;
      const key = entry.match_key.trim().toLowerCase();
      const list = byMatch.get(key) || [];
      list.push(entry);
      byMatch.set(key, list);
    }
    const nowMs = Date.now();
    return scheduleRows
      .map((match) => {
        const alliance = teamAllianceForMatch(match, normalizedTeamKey);
        if (!alliance) return null;
        const timer = liveTimerLabel(match.scheduled_time, nowMs);
        const completed = inferMatchCompleted(match, nowMs);
        const status: TeamMatchPerformanceRow['status'] = completed
          ? 'completed'
          : timer.state === 'live'
            ? 'live'
            : timer.state === 'upcoming'
              ? 'upcoming'
              : 'unknown';
        const winnerAlliance = inferWinnerAlliance(match, nowMs);
        const matchEntries = byMatch.get(match.match_key.toLowerCase()) || [];
        const overallValues = matchEntries.map((entry) => entry.overall_scout_rating.score_0_100);
        const manualValues = matchEntries.map((entry) => entry.manual_rating.score_0_100);
        const apiValues = matchEntries
          .map((entry) => entry.scouting_api_rating?.score_0_100 ?? null)
          .filter((value): value is number => typeof value === 'number');
        const pointValues = matchEntries.map((entry) => entry.points.total);
        const latestNote = matchEntries
          .slice()
          .sort((a, b) => b.saved_at_ms - a.saved_at_ms)
          .map((entry) => entry.notes)
          .find((value) => value.trim().length > 0) || null;
        const redScore = parseNumber(match.red_score);
        const blueScore = parseNumber(match.blue_score);
        const allianceScore = alliance === 'red' ? redScore : blueScore;
        const opponentScore = alliance === 'red' ? blueScore : redScore;
        return {
          match_key: match.match_key.toLowerCase(),
          match_display: match.display_name,
          comp_level: compLevelLabel(match.comp_level),
          scheduled_time: match.scheduled_time,
          status,
          alliance,
          alliance_score: allianceScore,
          opponent_score: opponentScore,
          winner_alliance: winnerAlliance,
          scout_reports: matchEntries.length,
          overall_scout_avg_0_100:
            overallValues.length > 0
              ? Number((overallValues.reduce((sum, value) => sum + value, 0) / overallValues.length).toFixed(1))
              : null,
          manual_avg_0_100:
            manualValues.length > 0
              ? Number((manualValues.reduce((sum, value) => sum + value, 0) / manualValues.length).toFixed(1))
              : null,
          scouting_api_avg_0_100:
            apiValues.length > 0
              ? Number((apiValues.reduce((sum, value) => sum + value, 0) / apiValues.length).toFixed(1))
              : null,
          avg_points_total:
            pointValues.length > 0
              ? Number((pointValues.reduce((sum, value) => sum + value, 0) / pointValues.length).toFixed(1))
              : null,
          latest_note: latestNote,
        } satisfies TeamMatchPerformanceRow;
      })
      .filter((value): value is TeamMatchPerformanceRow => Boolean(value))
      .sort((a, b) => {
        const aTime = a.scheduled_time || Number.MAX_SAFE_INTEGER;
        const bTime = b.scheduled_time || Number.MAX_SAFE_INTEGER;
        if (aTime !== bTime) return aTime - bTime;
        return a.match_key.localeCompare(b.match_key);
      });
  }, [entries, scheduleRows, selectedEventKey, selectedTeamKey]);

  const latestSavedEntry = useMemo(
    () => entries.find((entry) => entry.id === lastSavedEntryId) || null,
    [entries, lastSavedEntryId],
  );

  const teamRollups = useMemo(() => {
    const rollupMap = new Map<string, {
      team_key: string;
      team_label: string;
      latest_event_key: string;
      latest_match_key: string;
      latest_match_display: string;
      latest_saved_at_ms: number;
      entries_count: number;
      sum_total: number;
      sum_driver: number;
      sum_manual: number;
      sum_overall: number;
      sum_api: number;
      api_count: number;
      strong_count: number;
      risk_count: number;
      notes_count: number;
    }>();

    for (const entry of scopedEntries) {
      const key = entry.team_key;
      if (!key) continue;
      const row = rollupMap.get(key) || {
        team_key: entry.team_key,
        team_label: entry.team_label,
        latest_event_key: entry.event_key,
        latest_match_key: entry.match_key,
        latest_match_display: entry.match_display,
        latest_saved_at_ms: entry.saved_at_ms,
        entries_count: 0,
        sum_total: 0,
        sum_driver: 0,
        sum_manual: 0,
        sum_overall: 0,
        sum_api: 0,
        api_count: 0,
        strong_count: 0,
        risk_count: 0,
        notes_count: 0,
      };
      row.entries_count += 1;
      row.sum_total += entry.points.total;
      row.sum_driver += entry.driver_competency.score_0_100;
      row.sum_manual += entry.manual_rating.score_0_100;
      row.sum_overall += entry.overall_scout_rating.score_0_100;
      if (entry.scouting_api_rating) {
        row.sum_api += entry.scouting_api_rating.score_0_100;
        row.api_count += 1;
      }
      if (entry.manual_rating.score_0_100 >= 75) row.strong_count += 1;
      if (entry.manual_rating.score_0_100 <= 48) row.risk_count += 1;
      if (entry.notes.trim()) row.notes_count += 1;
      if (entry.saved_at_ms >= row.latest_saved_at_ms) {
        row.latest_saved_at_ms = entry.saved_at_ms;
        row.latest_event_key = entry.event_key;
        row.latest_match_key = entry.match_key;
        row.latest_match_display = entry.match_display;
        row.team_label = entry.team_label;
      }
      rollupMap.set(key, row);
    }

    const normalized = Array.from(rollupMap.values()).map((row) => {
      const avgApi = row.api_count > 0 ? row.sum_api / row.api_count : null;
      const avgOverall = row.sum_overall / row.entries_count;
      let headUp = 'Balanced profile; gather one more scout sample for confidence.';
      if (avgApi !== null && avgApi >= 80) {
        headUp = 'High-priority target with strong manual + API alignment.';
      } else if (avgOverall >= 82) {
        headUp = 'High-priority scouting target from room inputs; verify alliance fit.';
      } else if (row.strong_count >= Math.ceil(row.entries_count * 0.6)) {
        headUp = 'Consistently strong manual reports from scouts.';
      } else if (row.risk_count >= Math.ceil(row.entries_count * 0.45)) {
        headUp = 'Frequent risk flags in scouting reports; review clips before pick.';
      }
      return {
        team_key: row.team_key,
        team_label: row.team_label,
        entries_count: row.entries_count,
        latest_event_key: row.latest_event_key,
        latest_match_key: row.latest_match_key,
        latest_match_display: row.latest_match_display,
        latest_saved_at_ms: row.latest_saved_at_ms,
        avg_points_total: Number((row.sum_total / row.entries_count).toFixed(1)),
        avg_driver_0_100: Number((row.sum_driver / row.entries_count).toFixed(1)),
        avg_manual_0_100: Number((row.sum_manual / row.entries_count).toFixed(1)),
        avg_overall_scout_0_100: Number(avgOverall.toFixed(1)),
        avg_scouting_api_0_100: avgApi === null ? null : Number(avgApi.toFixed(1)),
        head_up: headUp,
        notes_count: row.notes_count,
      } satisfies TeamRollup;
    });

    const query = quickTeamQuery.trim().toLowerCase();
    const filtered = !query
      ? normalized
      : normalized.filter((row) => row.team_key.includes(query) || row.team_label.toLowerCase().includes(query));

    return filtered.sort((a, b) => {
      const aScore = a.avg_overall_scout_0_100;
      const bScore = b.avg_overall_scout_0_100;
      if (aScore !== bScore) return bScore - aScore;
      return b.latest_saved_at_ms - a.latest_saved_at_ms;
    });
  }, [quickTeamQuery, scopedEntries]);

  const teamSummaries = useMemo(() => {
    const grouped = new Map<string, SavedScoutingEntry[]>();
    for (const entry of scopedEntries) {
      const key = entry.team_key;
      if (!key) continue;
      const list = grouped.get(key) || [];
      list.push(entry);
      grouped.set(key, list);
    }

    const rows: TeamSummaryRow[] = [];
    for (const [teamKey, teamEntries] of grouped.entries()) {
      const sorted = teamEntries.slice().sort((a, b) => b.saved_at_ms - a.saved_at_ms);
      const latest = sorted[0];
      if (!latest) continue;
      const overallAvg =
        teamEntries.reduce((sum, entry) => sum + entry.overall_scout_rating.score_0_100, 0) / teamEntries.length;
      const manualAvg = teamEntries.reduce((sum, entry) => sum + entry.manual_rating.score_0_100, 0) / teamEntries.length;
      const driverAvg = teamEntries.reduce((sum, entry) => sum + entry.driver_competency.score_0_100, 0) / teamEntries.length;
      const pointsAvg = teamEntries.reduce((sum, entry) => sum + entry.points.total, 0) / teamEntries.length;
      const apiValues = teamEntries
        .map((entry) => entry.scouting_api_rating?.score_0_100 ?? null)
        .filter((value): value is number => typeof value === 'number');
      const apiAvg = apiValues.length > 0 ? apiValues.reduce((sum, value) => sum + value, 0) / apiValues.length : null;
      const endgameModeCounts = new Map<EndgameMode, number>();
      for (const entry of teamEntries) {
        const mode = entry.form.endgame_mode;
        endgameModeCounts.set(mode, (endgameModeCounts.get(mode) || 0) + 1);
      }
      const preferredEndgame = Array.from(endgameModeCounts.entries()).sort((a, b) => b[1] - a[1])[0]?.[0] || 'none';
      const noteSet = new Set<string>();
      for (const entry of sorted) {
        if (!entry.notes.trim()) continue;
        noteSet.add(entry.notes.trim());
        if (noteSet.size >= 3) break;
      }
      const notes = Array.from(noteSet);
      const summary =
        `${latest.team_key.toUpperCase()} averages ${overallAvg.toFixed(1)} overall (scout data)` +
        `, ${manualAvg.toFixed(1)} manual` +
        `${apiAvg !== null ? ` / ${apiAvg.toFixed(1)} Scout+API` : ''}` +
        ` across ${teamEntries.length} report${teamEntries.length === 1 ? '' : 's'}, with ${pointsAvg.toFixed(1)} avg points, ` +
        `${driverAvg.toFixed(1)} driver score, and typical endgame: ${ENDGAME_LABELS[preferredEndgame]}.`;

      rows.push({
        team_key: teamKey,
        team_label: latest.team_label,
        reports: teamEntries.length,
        avg_overall_scout_0_100: Number(overallAvg.toFixed(1)),
        avg_manual_0_100: Number(manualAvg.toFixed(1)),
        avg_scouting_api_0_100: apiAvg === null ? null : Number(apiAvg.toFixed(1)),
        avg_driver_0_100: Number(driverAvg.toFixed(1)),
        avg_points_total: Number(pointsAvg.toFixed(1)),
        endgame_mode: preferredEndgame,
        summary,
        notes,
      });
    }

    return rows.sort((a, b) => {
      const aScore = a.avg_overall_scout_0_100;
      const bScore = b.avg_overall_scout_0_100;
      if (aScore !== bScore) return bScore - aScore;
      return b.reports - a.reports;
    });
  }, [scopedEntries]);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    window.localStorage.setItem(
      SCOUTING_MOBILE_COMPACT_STORAGE,
      mobileCompactMode ? 'true' : 'false',
    );
  }, [mobileCompactMode]);

  useEffect(() => {
    if (typeof document === 'undefined') return undefined;

    // The product shell scrolls on its own root element (not `.ps-content`,
    // which is in-flow and never scrolls). Resolve the real scrolling ancestor
    // from the content node so the auto-collapse actually fires; fall back to
    // the document scroller. Without this the setup header never minimized.
    const contentNode = document.querySelector('.ps-content') as HTMLElement | null;
    const resolveScroller = (): HTMLElement => {
      let node: HTMLElement | null = contentNode?.parentElement ?? null;
      while (node && node !== document.body) {
        const style = getComputedStyle(node);
        const oy = style.overflowY;
        if ((oy === 'auto' || oy === 'scroll') && node.scrollHeight > node.clientHeight + 4) {
          return node;
        }
        node = node.parentElement;
      }
      return (document.scrollingElement as HTMLElement) || document.documentElement;
    };
    const scrollContainer = resolveScroller();
    const isDocScroller =
      scrollContainer === document.documentElement ||
      scrollContainer === document.scrollingElement ||
      scrollContainer === document.body;
    const scrollEventTarget: HTMLElement | Window = isDocScroller ? window : scrollContainer;

    let ticking = false;
    let lastScrollTop = scrollContainer.scrollTop;

    const updateChromeState = () => {
      const nextScrollTop = scrollContainer.scrollTop;
      const nextCondensed = isMobileLayout && nextScrollTop > 44;
      const hideThreshold = Math.min(140, Math.max(84, Math.round(window.innerHeight * 0.18)));
      setScoutingTopCondensed((current) => (current === nextCondensed ? current : nextCondensed));

      if (nextScrollTop <= 12) {
        setScoutingTopHidden(false);
        lastScrollTop = nextScrollTop;
        return;
      }

      const scrollDelta = nextScrollTop - lastScrollTop;
      if (scrollDelta > 10 && nextScrollTop > hideThreshold) {
        setScoutingTopHidden(true);
      } else if (scrollDelta < -8) {
        setScoutingTopHidden(false);
      }

      lastScrollTop = nextScrollTop;
    };

    const onScroll = () => {
      if (ticking) return;
      ticking = true;
      requestAnimationFrame(() => {
        updateChromeState();
        ticking = false;
      });
    };

    updateChromeState();
    scrollEventTarget.addEventListener('scroll', onScroll, { passive: true });

    return () => {
      scrollEventTarget.removeEventListener('scroll', onScroll);
    };
  }, [isMobileLayout]);

  useEffect(() => {
    if (!isMobileLayout) return;
    setScoutingTopHidden(false);
    setSetupHeaderManuallyCollapsed(false);
  }, [isMobileLayout, mobileFinderOpen]);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    try {
      window.localStorage.setItem(
        SCOUTING_MOBILE_PANEL_PREFS_STORAGE,
        JSON.stringify({
          finderOpen: mobileFinderOpen,
          section: mobileScoutSection,
          capture: mobileCapturePanel,
          score: mobileScorePanel,
          history: mobileHistoryPanel,
        } satisfies StoredMobilePanelPrefs),
      );
    } catch {
      // ignore storage failures
    }
  }, [mobileCapturePanel, mobileFinderOpen, mobileHistoryPanel, mobileScoutSection, mobileScorePanel]);

  useEffect(() => {
    if (!isMobileLayout) return;
    if (mobileScoutSection === 'history') {
      setMobileScoutSection('capture');
    }
    if (mobileScorePanel === 'driver' || mobileScorePanel === 'saved') {
      setMobileScorePanel('points');
    }
  }, [isMobileLayout, mobileScoutSection, mobileScorePanel]);

  useEffect(() => {
    if (!pageVisible) return;
    const timer = window.setInterval(() => {
      setTimerSec((current) => {
        if (!timerRunning) return current;
        if (current <= 0) return 0;
        return current - 1;
      });
    }, 1000);
    return () => window.clearInterval(timer);
  }, [pageVisible, timerRunning]);

  useEffect(() => {
    if (timerSec <= 0 && timerRunning) {
      setTimerRunning(false);
    }
  }, [timerRunning, timerSec]);

  useEffect(() => {
    if (!floatingTimerDragging) return;
    const onPointerMove = (event: PointerEvent) => {
      const drag = floatingTimerDragRef.current;
      if (!drag || drag.mode !== 'pointer' || drag.pointerId !== event.pointerId) return;
      const next = clampFloatingTimerToViewport({
        x: drag.startX + (event.clientX - drag.startClientX),
        y: drag.startY + (event.clientY - drag.startClientY),
      });
      setFloatingTimerPosition(next);
      if (event.pointerType === 'touch') {
        event.preventDefault();
      }
    };
    const onPointerEnd = (event: PointerEvent) => {
      const drag = floatingTimerDragRef.current;
      if (!drag || drag.mode !== 'pointer' || drag.pointerId !== event.pointerId) return;
      floatingTimerDragRef.current = null;
      setFloatingTimerDragging(false);
    };
    const onTouchMove = (event: TouchEvent) => {
      const drag = floatingTimerDragRef.current;
      if (!drag || drag.mode !== 'touch') return;
      const touch = Array.from(event.changedTouches).find((item) => item.identifier === drag.touchId);
      if (!touch) return;
      const next = clampFloatingTimerToViewport({
        x: drag.startX + (touch.clientX - drag.startClientX),
        y: drag.startY + (touch.clientY - drag.startClientY),
      });
      setFloatingTimerPosition(next);
      event.preventDefault();
    };
    const onTouchEnd = (event: TouchEvent) => {
      const drag = floatingTimerDragRef.current;
      if (!drag || drag.mode !== 'touch') return;
      const ended = Array.from(event.changedTouches).some((item) => item.identifier === drag.touchId);
      if (!ended) return;
      floatingTimerDragRef.current = null;
      setFloatingTimerDragging(false);
    };
    window.addEventListener('pointermove', onPointerMove);
    window.addEventListener('pointerup', onPointerEnd);
    window.addEventListener('pointercancel', onPointerEnd);
    if (!supportsPointerEvents) {
      window.addEventListener('touchmove', onTouchMove, { passive: false });
      window.addEventListener('touchend', onTouchEnd);
      window.addEventListener('touchcancel', onTouchEnd);
    }
    return () => {
      window.removeEventListener('pointermove', onPointerMove);
      window.removeEventListener('pointerup', onPointerEnd);
      window.removeEventListener('pointercancel', onPointerEnd);
      if (!supportsPointerEvents) {
        window.removeEventListener('touchmove', onTouchMove);
        window.removeEventListener('touchend', onTouchEnd);
        window.removeEventListener('touchcancel', onTouchEnd);
      }
    };
  }, [floatingTimerDragging, supportsPointerEvents]);

  useEffect(() => {
    window.localStorage.setItem(SCOUTING_TIMER_FLOAT_STORAGE, JSON.stringify(floatingTimerPosition));
  }, [floatingTimerPosition]);

  useEffect(() => {
    const onResize = () => {
      setFloatingTimerPosition((current) => clampFloatingTimerToViewport(current));
    };
    onResize();
    window.addEventListener('resize', onResize, { passive: true });
    return () => window.removeEventListener('resize', onResize);
  }, []);

  useEffect(() => {
    window.localStorage.setItem(SCOUTING_ENTRIES_STORAGE, JSON.stringify(entries));
  }, [entries]);

  useEffect(() => {
    if (!scoutProfile) {
      window.localStorage.removeItem(SCOUT_PROFILE_STORAGE);
      return;
    }
    window.localStorage.setItem(SCOUT_PROFILE_STORAGE, scoutProfile);
  }, [scoutProfile]);

  useEffect(() => {
    if (!myTeamKey) {
      window.localStorage.removeItem(SCOUT_MY_TEAM_STORAGE);
      return;
    }
    window.localStorage.setItem(SCOUT_MY_TEAM_STORAGE, myTeamKey);
  }, [myTeamKey]);

  useEffect(() => {
    const next = new URLSearchParams();
    if (selectedEventKey) next.set('event', selectedEventKey);
    if (selectedMatchKey) next.set('match', selectedMatchKey);
    if (selectedTeamKey) next.set('team', selectedTeamKey);
    if (myTeamKey) next.set('my_team', myTeamKey);
    const nextValue = next.toString();
    if (nextValue !== searchParams.toString()) {
      setSearchParams(next, { replace: true });
    }
    writeCenterContext({
      eventKey: selectedEventKey,
      matchKey: selectedMatchKey,
      teamKey: selectedTeamKey,
      sourcePath: '/scouting',
    });
  }, [myTeamKey, searchParams, selectedEventKey, selectedMatchKey, selectedTeamKey, setSearchParams]);

  useEffect(() => {
    const activeRoomKey = normalizeRoomKey(activeRoom?.room_key || '');
    try {
      // Legacy cleanup: room restore is session-scoped now, not cross-session.
      window.localStorage.removeItem(SCOUTING_ROOM_KEY_STORAGE);
    } catch {
      // ignore storage failures
    }
    // Do not clear session-scoped room key during route/tab switches.
    // It should persist for the browser tab lifetime and only be cleared on explicit leave.
    if (!activeRoomKey) return;
    window.sessionStorage.setItem(SCOUTING_ROOM_KEY_STORAGE, activeRoomKey);
  }, [activeRoom?.room_key]);

  useEffect(() => {
    if (restoredRoomRef.current) return;
    const storedRoomKey = normalizeRoomKey(window.sessionStorage.getItem(SCOUTING_ROOM_KEY_STORAGE) || '');
    if (!storedRoomKey) {
      restoredRoomRef.current = true;
      return;
    }
    if (!hasScoutProfile) {
      setRoomErrorText('Enter your scout name before restoring a room.');
      return;
    }
    restoredRoomRef.current = true;
    let cancelled = false;
    setRoomKeyInput(storedRoomKey);
    setRoomErrorText('');
    void (async () => {
      try {
        const storedAccessToken = getStoredRoomAccessToken(storedRoomKey);
        const response = await createOrJoinScoutingRoom({
          room_key: storedRoomKey,
          event_key: selectedEventKey || undefined,
          scout_profile: scoutProfile,
          client_id: roomClientId,
          title: selectedEventKey ? `Scouting ${selectedEventKey}` : undefined,
          create_if_missing: false,
          room_access_token: storedAccessToken || undefined,
          timeoutMs: 25000,
        });
        if (cancelled) return;
        const room = response.room;
        const joinedRoomKey = normalizeRoomKey(room.room_key);
        setActiveRoom(room);
        applyRoomAccess(joinedRoomKey, response.access || null);
        setRoomKeyInput(joinedRoomKey || room.room_key);
        setRoomPresence(Array.isArray(room.presence) ? room.presence : []);
        setRoomAssignments(Array.isArray(response.assignments) ? response.assignments : []);
        const roomEntries = entriesFromRoomRecords(response.entries || [], joinedRoomKey);
        setEntries((current) => {
          const withoutJoinedRoomEntries = stripEntriesForRoom(current, joinedRoomKey);
          if (roomEntries.length === 0) return withoutJoinedRoomEntries;
          return mergeEntries(withoutJoinedRoomEntries, roomEntries);
        });
        setHistoryMineOnly(false);
        setRoomSocketNonce((current) => current + 1);
        setStatusText(`Restored room ${room.room_key}.`);
      } catch (error) {
        if (cancelled) return;
        const detail = roomRequestErrorMessage(
          (error as Error).message || 'Unable to restore scouting room.',
          'Restoring the scouting room',
        );
        if (detail.toLowerCase().includes('not found')) {
          try {
            window.sessionStorage.removeItem(SCOUTING_ROOM_KEY_STORAGE);
          } catch {
            // ignore storage failures
          }
          setRoomKeyInput('');
        }
        setRoomErrorText(detail);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [applyRoomAccess, hasScoutProfile, roomClientId, scoutProfile, selectedEventKey]);

  useEffect(() => {
    let cancelled = false;
    async function run() {
      setLoadingSuggested(true);
      try {
        const payload = await getSuggestedEvents(CURRENT_SEASON_YEAR, FALLBACK_SEASON_YEAR, 80);
        if (cancelled) return;
        if (!selectedEventKey && payload.events.length > 0) {
          const nextEventKey = normalizeEventKeyInput(payload.events[0].event_key);
          setEventInput(nextEventKey);
          setSelectedEventKey(nextEventKey);
        }
      } catch (error) {
        if (cancelled) return;
        setErrorText((error as Error).message || 'Unable to load suggested events.');
      } finally {
        if (!cancelled) setLoadingSuggested(false);
      }
    }
    void run();
    return () => {
      cancelled = true;
    };
  }, [selectedEventKey]);

  useEffect(() => {
    if (!selectedEventKey) {
      setScheduleRows([]);
      setScheduleName(null);
      return;
    }
    let cancelled = false;
    async function run() {
      setLoadingSchedule(true);
      setErrorText('');
      try {
        const payload = await getEventSchedule(selectedEventKey, false);
        if (cancelled) return;
        const rows = payload.matches || [];
        setScheduleRows(rows);
        setScheduleName(payload.event_name || null);
        setStatusText(
          rows.length > 0
            ? `${rows.length} matches for ${selectedEventKey}.`
            : `No schedule for ${selectedEventKey}.`,
        );
        setSelectedMatchKey((current) => {
          const normalizedCurrent = current.trim().toLowerCase();
          if (normalizedCurrent && rows.some((row) => row.match_key.toLowerCase() === normalizedCurrent)) {
            return normalizedCurrent;
          }
          return preferredMatchKey(rows).toLowerCase();
        });
      } catch (error) {
        if (cancelled) return;
        setScheduleRows([]);
        setScheduleName(null);
        setErrorText((error as Error).message || `Unable to load schedule for ${selectedEventKey}.`);
      } finally {
        if (!cancelled) setLoadingSchedule(false);
      }
    }
    void run();
    return () => {
      cancelled = true;
    };
  }, [selectedEventKey, eventFetchCtr]);

  useEffect(() => {
    if (!selectedEventKey) {
      setEventApiBaselineByTeam({});
      setEventIntelError('');
      return;
    }
    let cancelled = false;
    async function run() {
      setLoadingEventIntel(true);
      setEventIntelError('');
      try {
        const payload = await getEventTeamsIntel(selectedEventKey, {
          include_tba: true,
          include_statbotics: false,
          include_season_fallback: true,
          include_rating_details: false,
          include_rating_signals: false,
          auto_heal_ratings: true,
        });
        if (cancelled) return;
        const map: Record<string, EventTeamApiBaseline> = {};
        const teams = Array.isArray(payload.teams) ? payload.teams : [];
        for (const value of teams) {
          const row = asRecord(value);
          if (!row) continue;
          const teamKey = typeof row.team_key === 'string' ? row.team_key.toLowerCase() : '';
          if (!teamKey) continue;
          const rating = asRecord(row.rating);
          const analysis = asRecord(row.analysis);
          const averages = asRecord(analysis?.averages);
          const ratingScore = parseNumber(rating?.rating_0_100);
          const ratingDerivedNormEpa = ratingScore !== null ? (1200 + ratingScore * 8.5) : null;
          map[teamKey] = {
            rating_0_100: ratingScore,
            reliability_0_1: parseNumber(averages?.reliability_score) ?? parseNumber(analysis?.reliability_score),
            statbotics_norm_epa: ratingDerivedNormEpa,
          };
        }
        setEventApiBaselineByTeam(map);
      } catch (error) {
        if (cancelled) return;
        setEventApiBaselineByTeam({});
        setEventIntelError((error as Error).message || `Unable to load team intel for ${selectedEventKey}.`);
      } finally {
        if (!cancelled) setLoadingEventIntel(false);
      }
    }
    void run();
    return () => {
      cancelled = true;
    };
  }, [selectedEventKey]);

  useEffect(() => {
    const roomKey = activeRoom?.room_key || '';
    if (roomReconnectTimerRef.current !== null) {
      window.clearTimeout(roomReconnectTimerRef.current);
      roomReconnectTimerRef.current = null;
    }
    if (!roomKey) {
      setRoomConnectionState('disconnected');
      setRoomPresence([]);
      setRoomAssignments([]);
      setActiveRoomAccess(null);
      setRoomHttpFallbackActive(false);
      roomReconnectAttemptsRef.current = 0;
      return;
    }
    if (!hasScoutProfile) {
      setRoomConnectionState('error');
      setRoomErrorText('Enter your scout name before connecting to a room.');
      roomReconnectAttemptsRef.current = 0;
      return;
    }
    const roomAccessToken = resolveRoomAccessToken(roomKey);
    if (!roomAccessToken) {
      let cancelled = false;
      setRoomConnectionState('connecting');
      setRoomErrorText('Refreshing room access...');
      void (async () => {
        const refreshedToken = await refreshRoomAccessSession(roomKey, { silent: true });
        if (cancelled) return;
        if (refreshedToken) {
          setRoomErrorText('');
          setStatusText('Room session refreshed. Connecting...');
          setRoomSocketNonce((current) => current + 1);
          return;
        }
        setRoomConnectionState('error');
        setRoomErrorText('Room access token missing or expired. Re-join room to continue.');
        roomReconnectAttemptsRef.current = 0;
      })();
      return () => {
        cancelled = true;
      };
    }
    if (ROOM_WS_DISABLED) {
      if (!roomHttpFallbackActive) setRoomHttpFallbackActive(true);
      setRoomConnectionState('connected');
      setRoomErrorText('');
      return;
    }
    if (roomHttpFallbackActive) {
      setRoomConnectionState('connected');
      return;
    }

    let closedByEffect = false;
    let heartbeatTimer: number | null = null;

    const clearHeartbeat = () => {
      if (heartbeatTimer !== null) {
        window.clearInterval(heartbeatTimer);
        heartbeatTimer = null;
      }
    };
    const scheduleReconnect = (reason: string) => {
      if (closedByEffect) return;
      const attempt = roomReconnectAttemptsRef.current + 1;
      roomReconnectAttemptsRef.current = attempt;
      if (attempt > ROOM_WS_MAX_RECONNECT_ATTEMPTS) {
        setRoomConnectionState('error');
        setRoomErrorText(
          `Room websocket disconnected after ${ROOM_WS_MAX_RECONNECT_ATTEMPTS} retries. ${reason}`,
        );
        return;
      }
      const baseDelayMs = Math.min(12000, Math.round(700 * (1.75 ** (attempt - 1))));
      const jitterFactor = 0.85 + (Math.random() * 0.4);
      const delayMs = Math.min(15000, Math.max(350, Math.round(baseDelayMs * jitterFactor)));
      setRoomConnectionState('connecting');
      setStatusText(
        `Room socket lost (${reason}). Reconnecting in ${(delayMs / 1000).toFixed(1)}s (${attempt}/${ROOM_WS_MAX_RECONNECT_ATTEMPTS}).`,
      );
      roomReconnectTimerRef.current = window.setTimeout(() => {
        roomReconnectTimerRef.current = null;
        setRoomSocketNonce((current) => current + 1);
      }, delayMs);
    };

    setRoomConnectionState('connecting');
    setRoomErrorText('');
    const roomWsUrl = scoutingRoomWebSocketUrl(roomKey, {
      scout_profile: scoutProfile,
      event_key: selectedEventKey || activeRoom?.event_key || undefined,
      client_id: roomClientId,
      history_limit: 220,
      room_access_token: roomAccessToken,
    });
    const socket = new WebSocket(roomWsUrl);
    let proxyUpgradeLikelyBlocked = false;
    try {
      const parsedWsUrl = new URL(roomWsUrl);
      proxyUpgradeLikelyBlocked =
        typeof window !== 'undefined' &&
        window.location.protocol === 'https:' &&
        parsedWsUrl.host === window.location.host &&
        parsedWsUrl.pathname.startsWith('/api/');
    } catch {
      proxyUpgradeLikelyBlocked = false;
    }

    socket.onopen = () => {
      if (closedByEffect) return;
      roomReconnectAttemptsRef.current = 0;
      setRoomConnectionState('connected');
      setRoomErrorText('');
      setRoomHttpFallbackActive(false);
      clearHeartbeat();
      heartbeatTimer = window.setInterval(() => {
        if (closedByEffect) return;
        if (socket.readyState !== WebSocket.OPEN) return;
        try {
          socket.send(JSON.stringify({ type: 'ping' }));
        } catch {
          // no-op; close handler will trigger reconnect path if needed
        }
      }, ROOM_WS_HEARTBEAT_MS);
    };

    socket.onmessage = (event) => {
      if (closedByEffect) return;
      try {
        const payload = JSON.parse(String(event.data || '{}')) as Record<string, unknown>;
        const messageType = String(payload.type || '').toLowerCase();
        if (messageType === 'pong' || messageType === 'entry_ack') {
          return;
        }
        if (messageType === 'error') {
          const detail = String(payload.detail || 'Room websocket returned an error.');
          setRoomErrorText(detail);
          return;
        }
        if (messageType === 'snapshot') {
          const roomPayload = payload.room as ScoutingRoomMeta | undefined;
          if (roomPayload && typeof roomPayload.room_key === 'string') {
            setActiveRoom(roomPayload);
            setRoomPresence(Array.isArray(roomPayload.presence) ? roomPayload.presence : []);
          }
          const assignmentRows = Array.isArray(payload.assignments)
            ? (payload.assignments as ScoutingRoomAssignmentRecord[])
            : [];
          setRoomAssignments(assignmentRows);
          const snapshotRoomKey = normalizeRoomKey(roomPayload?.room_key || roomKey);
          const records = Array.isArray(payload.entries)
            ? (payload.entries as ScoutingRoomEntryRecord[])
            : [];
          const normalized = entriesFromRoomRecords(records, snapshotRoomKey);
          setEntries((current) =>
            normalized.length > 0
              ? mergeEntries(stripEntriesForRoom(current, snapshotRoomKey), normalized)
              : stripEntriesForRoom(current, snapshotRoomKey),
          );
          return;
        }
        if (messageType === 'presence') {
          const members = Array.isArray(payload.members)
            ? (payload.members as ScoutingRoomPresenceMember[])
            : [];
          setRoomPresence(members);
          const leaderScoutProfile = String(payload.leader_scout_profile || '').trim();
          const leaderSource = String(payload.leader_source || '').trim();
          if (leaderScoutProfile || leaderSource) {
            setActiveRoom((current) => (
              current
                ? {
                  ...current,
                  leader_scout_profile: leaderScoutProfile || current.leader_scout_profile || null,
                  leader_source: leaderSource || current.leader_source || null,
                }
                : current
            ));
          }
          return;
        }
        if (messageType === 'secondary_leaders_updated') {
          const nextSecondaryLeaders = Array.isArray(payload.secondary_leader_scout_profiles)
            ? payload.secondary_leader_scout_profiles.map((profile) => normalizeScoutProfile(String(profile || ''))).filter(Boolean)
            : [];
          setActiveRoom((current) => (
            current
              ? {
                ...current,
                secondary_leader_scout_profiles: nextSecondaryLeaders,
              }
              : current
          ));
          const action = String(payload.action || '').trim().toLowerCase();
          const targetScoutProfile = normalizeScoutProfile(String(payload.target_scout_profile || ''));
          if (targetScoutProfile) {
            if (action === 'promoted') setStatusText(`${targetScoutProfile} is now a secondary leader.`);
            if (action === 'demoted') setStatusText(`${targetScoutProfile} is no longer a secondary leader.`);
          }
          return;
        }
        if (messageType === 'assignment_updated') {
          const assignment = payload.assignment as ScoutingRoomAssignmentRecord | undefined;
          if (!assignment || !assignment.match_key || !assignment.team_key) return;
          const assignmentMatchKey = String(assignment.match_key || '').trim().toLowerCase();
          const assignmentTeamKey = String(assignment.team_key || '').trim().toLowerCase();
          if (!assignmentMatchKey || !assignmentTeamKey) return;
          setRoomAssignments((current) => {
            const next = current.filter((row) => (
              String(row.match_key || '').trim().toLowerCase() !== assignmentMatchKey
              || String(row.team_key || '').trim().toLowerCase() !== assignmentTeamKey
            ));
            next.push(assignment);
            return next;
          });
          return;
        }
        if (messageType === 'assignment_deleted') {
          const assignment = asRecord(payload.assignment);
          const assignmentMatchKey = String(assignment?.match_key || '').trim().toLowerCase();
          const assignmentTeamKey = String(assignment?.team_key || '').trim().toLowerCase();
          if (!assignmentMatchKey || !assignmentTeamKey) return;
          setRoomAssignments((current) => current.filter((row) => (
            String(row.match_key || '').trim().toLowerCase() !== assignmentMatchKey
            || String(row.team_key || '').trim().toLowerCase() !== assignmentTeamKey
          )));
          return;
        }
        if (messageType === 'assignments_replaced') {
          const assignmentRows = Array.isArray(payload.assignments)
            ? (payload.assignments as ScoutingRoomAssignmentRecord[])
            : [];
          setRoomAssignments(assignmentRows);
          return;
        }
        if (messageType === 'member_kicked') {
          const targetScoutProfile = normalizeScoutProfile(String(payload.target_scout_profile || ''));
          const kickedBy = normalizeScoutProfile(String(payload.kicked_by || ''));
          if (targetScoutProfile) {
            setStatusText(
              kickedBy
                ? `${targetScoutProfile} was removed from room by ${kickedBy}.`
                : `${targetScoutProfile} was removed from room.`,
            );
          }
          return;
        }
        if (messageType === 'entry_saved') {
          const serverEntry = asRecord(payload.server_entry);
          const roomEntry = asRecord(serverEntry?.entry) || asRecord(payload.entry);
          if (!roomEntry) return;
          const normalized = normalizeEntry(roomEntry);
          if (!normalized) return;
          const syncRoomKey = normalizeRoomKey(String(serverEntry?.room_key || activeRoom?.room_key || roomKey || ''));
          const normalizedWithRoom = {
            ...normalized,
            room_key: syncRoomKey || normalized.room_key || null,
          } satisfies SavedScoutingEntry;
          setEntries((current) => mergeEntry(current, normalizedWithRoom));
          setLastSavedAt(Date.now());
          return;
        }
      } catch {
        setRoomErrorText('Received invalid room websocket payload.');
      }
    };

    socket.onerror = () => {
      if (closedByEffect) return;
      setRoomConnectionState('error');
      setRoomErrorText('Room websocket encountered an error. Attempting reconnect...');
    };

    socket.onclose = (event) => {
      if (closedByEffect) return;
      clearHeartbeat();
      setRoomConnectionState('disconnected');
      if (event.code === 1006 && proxyUpgradeLikelyBlocked) {
        roomReconnectAttemptsRef.current = 0;
        setRoomHttpFallbackActive(true);
        setRoomConnectionState('error');
        setRoomErrorText(
          'Room websocket failed through the frontend API proxy. Falling back to HTTP sync. Configure VITE_WS_URL (or NEXT_PUBLIC_WS_URL) to your backend wss:// endpoint for live socket mode.',
        );
        return;
      }
      if (event.code === 4400 || event.code === 4403) {
        if (event.code === 4403) {
          roomReconnectAttemptsRef.current = 0;
          setRoomConnectionState('connecting');
          void (async () => {
            const refreshedToken = await refreshRoomAccessSession(roomKey, { silent: true });
            if (closedByEffect) return;
            if (refreshedToken) {
              setRoomErrorText('');
              setStatusText('Room authorization refreshed. Reconnecting...');
              setRoomSocketNonce((current) => current + 1);
              return;
            }
            setRoomConnectionState('error');
            setRoomErrorText(event.reason || 'Room websocket authorization failed.');
          })();
          return;
        }
        roomReconnectAttemptsRef.current = 0;
        setRoomConnectionState('error');
        setRoomErrorText(event.reason || 'Room websocket request is invalid.');
        return;
      }
      const reason = event.reason
        ? `${event.code}: ${event.reason}`
        : `close code ${event.code || 1006}`;
      scheduleReconnect(reason);
    };

    return () => {
      closedByEffect = true;
      clearHeartbeat();
      try {
        socket.close();
      } catch {
        // no-op
      }
    };
  }, [
    activeRoom?.event_key,
    activeRoom?.room_key,
    activeRoomAccess,
    hasScoutProfile,
    roomClientId,
    roomHttpFallbackActive,
    roomSocketNonce,
    refreshRoomAccessSession,
    resolveRoomAccessToken,
    scoutProfile,
    selectedEventKey,
  ]);

  useEffect(() => {
    setRoomHttpFallbackActive(false);
    roomReconnectAttemptsRef.current = 0;
  }, [activeRoom?.room_key]);

  useEffect(() => {
    const roomKey = normalizeRoomKey(activeRoom?.room_key || '');
    if (!roomKey || !hasScoutProfile) return;
    const expiresAtUnix = Number(activeRoomAccess?.expires_at_unix || 0);
    if (!Number.isFinite(expiresAtUnix) || expiresAtUnix <= 0) return;
    const refreshAtMs = Math.floor(expiresAtUnix * 1000) - (ROOM_ACCESS_REFRESH_LEEWAY_SEC * 1000);
    const delayMs = Math.max(1000, refreshAtMs - Date.now());
    const timer = window.setTimeout(() => {
      void refreshRoomAccessSession(roomKey, { silent: true });
    }, delayMs);
    return () => {
      window.clearTimeout(timer);
    };
  }, [
    activeRoom?.room_key,
    activeRoomAccess?.expires_at_unix,
    hasScoutProfile,
    refreshRoomAccessSession,
  ]);

  useEffect(() => {
    const roomKey = normalizeRoomKey(activeRoom?.room_key || '');
    if (!roomHttpFallbackActive || !roomKey || !hasScoutProfile) return;

    let cancelled = false;
    let pollTimer: number | null = null;

    const pollRoomState = async () => {
      try {
        const response = await getScoutingRoomState(roomKey, 220, {
          scout_profile: scoutProfile,
          client_id: roomClientId,
          presence_heartbeat: true,
          room_access_token: resolveRoomAccessToken(roomKey) || undefined,
          timeoutMs: 22000,
        });
        if (cancelled) return;

        if (response.access) applyRoomAccess(roomKey, response.access);
        if (response.room && typeof response.room.room_key === 'string') {
          setActiveRoom(response.room);
          setRoomPresence(Array.isArray(response.room.presence) ? response.room.presence : []);
        }

        const assignments = Array.isArray(response.assignments)
          ? response.assignments
          : [];
        setRoomAssignments(assignments);

        const normalized = entriesFromRoomRecords(
          Array.isArray(response.entries) ? response.entries : [],
          roomKey,
        );
        setEntries((current) => (
          normalized.length > 0
            ? mergeEntries(stripEntriesForRoom(current, roomKey), normalized)
            : stripEntriesForRoom(current, roomKey)
        ));

        setRoomConnectionState('connected');
        setRoomErrorText('');
        setStatusText('Room live sync is running in compatibility mode (HTTP polling).');
      } catch (error) {
        if (cancelled) return;
        const detail = error instanceof Error ? error.message : 'Failed to refresh room state.';
        if (isRoomAccessAuthError(detail)) {
          setRoomConnectionState('connecting');
          const refreshedToken = await refreshRoomAccessSession(roomKey, { silent: true });
          if (cancelled) return;
          if (refreshedToken) {
            setRoomErrorText('');
            setStatusText('Room session refreshed. Retrying HTTP sync...');
            return;
          }
        }
        const detailLookup = String(detail || '').toLowerCase();
        const abortLike = detailLookup.includes('abort') || detailLookup.includes('signal is aborted');
        if (abortLike) {
          setRoomConnectionState('connecting');
          setRoomErrorText('');
          setStatusText('Room HTTP sync request timed out. Retrying...');
          return;
        }
        setRoomConnectionState('error');
        setRoomErrorText(`HTTP room sync failed: ${detail}`);
      } finally {
        if (!cancelled) {
          pollTimer = window.setTimeout(() => {
            void pollRoomState();
          }, ROOM_HTTP_FALLBACK_POLL_MS);
        }
      }
    };

    void pollRoomState();

    return () => {
      cancelled = true;
      if (pollTimer !== null) {
        window.clearTimeout(pollTimer);
      }
    };
  }, [
    activeRoom?.room_key,
    applyRoomAccess,
    hasScoutProfile,
    roomClientId,
    roomHttpFallbackActive,
    refreshRoomAccessSession,
    resolveRoomAccessToken,
    scoutProfile,
  ]);

  useEffect(() => {
    if (!teamOptions.some((team) => team.team_key === selectedTeamKey)) {
      setSelectedTeamKey(teamOptions[0]?.team_key || '');
    }
  }, [selectedTeamKey, teamOptions]);

  // myTeamKey persists across events — your FRC team number doesn't change.

  useEffect(() => {
    if (selectedTeamKey || !myTeamKey) return;
    const nextOpponent = upcomingOpponentOptions[0];
    if (!nextOpponent) return;
    setSelectedMatchKey(nextOpponent.next_match_key);
    setSelectedTeamKey(nextOpponent.team_key);
  }, [myTeamKey, selectedTeamKey, upcomingOpponentOptions]);

  useEffect(() => {
    if (!selectedMatchKey || !selectedTeamKey) return;
    setForm(EMPTY_FORM);
    setRpState(EMPTY_RP);
    setScoutNotes('');
    setTimerSec(MATCH_DURATION_SEC);
    setTimerRunning(false);
  }, [selectedMatchKey, selectedTeamKey]);

  function updateCounter(field: CounterField, nextValue: number) {
    const safe = Math.max(0, Math.floor(nextValue));
    setForm((current) => {
      const next = {
        ...current,
        [field]: safe,
      };
      if (field === 'teleop_under_defense_scored' && safe > next.teleop_under_defense_attempts) {
        next.teleop_under_defense_attempts = safe;
      }
      if (field === 'teleop_under_defense_attempts' && safe < next.teleop_under_defense_scored) {
        next.teleop_under_defense_scored = safe;
      }
      return next;
    });
  }

  function applySuggestedRp() {
    setRpState((current) => ({
      ...current,
      energized: rpSuggested.energized,
      supercharged: rpSuggested.supercharged,
      traversal: rpSuggested.traversal,
    }));
  }

  function jumpToNextMatchTarget() {
    if (!nextMatchTarget) return;
    setSelectedMatchKey(nextMatchTarget.match_key);
    const hasCurrentTeam = nextMatchTarget.teams.some((team) => team.team_key === selectedTeamKey);
    if (!hasCurrentTeam) {
      const fallbackTeam = nextMatchTarget.teams[0]?.team_key || '';
      if (fallbackTeam) setSelectedTeamKey(fallbackTeam);
    }
    setStatusText(`Loaded next match: ${nextMatchTarget.match_display}.`);
    if (isMobileLayout) setMobileFinderOpen(false);
  }

  function jumpToNextTeamTarget() {
    if (!nextTeamTarget?.team_key) return;
    if (nextTeamTarget.match_key) setSelectedMatchKey(nextTeamTarget.match_key);
    setSelectedTeamKey(nextTeamTarget.team_key);
    setStatusText(`Loaded next team: ${nextTeamTarget.team_key.toUpperCase()} (${nextTeamTarget.match_display}).`);
    if (isMobileLayout) setMobileFinderOpen(false);
  }

  function syncTimerToMatch() {
    if (!selectedMatch?.scheduled_time) {
      setTimerSec(MATCH_DURATION_SEC);
      setTimerRunning(false);
      return;
    }
    const nowSec = Math.floor(Date.now() / 1000);
    const elapsed = nowSec - selectedMatch.scheduled_time;
    const remaining = clampNumber(MATCH_DURATION_SEC - elapsed, 0, MATCH_DURATION_SEC);
    setTimerSec(remaining);
    setTimerRunning(elapsed >= 0 && remaining > 0);
  }

  async function createOrJoinRoom(options?: { overrideRoomKey?: string; restoring?: boolean }): Promise<{ ok: boolean; error?: string }> {
    if (!hasScoutProfile) {
      const detail = 'Enter your scout name before joining a room.';
      setRoomErrorText(detail);
      return { ok: false, error: detail };
    }
    const requestedRoomKey = normalizeRoomKey(options?.overrideRoomKey ?? roomKeyInput);
    const activeRoomKey = normalizeRoomKey(activeRoom?.room_key || '');
    const resolvedRoomKey = requestedRoomKey || activeRoomKey;
    const createIfMissing = !resolvedRoomKey;
    const roomAccessToken = resolvedRoomKey ? resolveRoomAccessToken(resolvedRoomKey) : '';
    try {
      setRoomErrorText('');
      const response = await createOrJoinScoutingRoom({
        room_key: resolvedRoomKey || undefined,
        event_key: selectedEventKey || undefined,
        scout_profile: scoutProfile,
        client_id: roomClientId,
        title: selectedEventKey ? `Scouting ${selectedEventKey}` : undefined,
        create_if_missing: createIfMissing,
        room_access_token: roomAccessToken || undefined,
        timeoutMs: 25000,
      });
      const room = response.room;
      const joinedRoomKey = normalizeRoomKey(room.room_key);
      const switchedRooms = Boolean(activeRoomKey) && Boolean(joinedRoomKey) && activeRoomKey !== joinedRoomKey;
      setActiveRoom(room);
      applyRoomAccess(joinedRoomKey, response.access || null);
      setRoomKeyInput(joinedRoomKey || room.room_key);
      setRoomPresence(Array.isArray(room.presence) ? room.presence : []);
      setRoomAssignments(Array.isArray(response.assignments) ? response.assignments : []);
      const roomEntries = entriesFromRoomRecords(response.entries || [], joinedRoomKey);
      setEntries((current) => {
        const withoutPrevious = switchedRooms ? stripEntriesForRoom(current, activeRoomKey) : current;
        const withoutJoinedRoomEntries = stripEntriesForRoom(withoutPrevious, joinedRoomKey);
        if (roomEntries.length === 0) return withoutJoinedRoomEntries;
        return mergeEntries(withoutJoinedRoomEntries, roomEntries);
      });
      if (switchedRooms) {
        setLastSavedEntryId('');
      }
      setHistoryMineOnly(false);
      roomReconnectAttemptsRef.current = 0;
      if (roomReconnectTimerRef.current !== null) {
        window.clearTimeout(roomReconnectTimerRef.current);
        roomReconnectTimerRef.current = null;
      }
      setRoomSocketNonce((current) => current + 1);
      if (options?.restoring) {
        setStatusText(`Restored room ${room.room_key}.`);
      } else if (switchedRooms) {
        setStatusText(
          `Switched to room ${room.room_key}. Reports sync live.`,
        );
      } else if (createIfMissing) {
        setStatusText(
          `Created room ${room.room_key}. Reports sync live.`,
        );
      } else {
        setStatusText(
          `Joined room ${room.room_key}. Reports sync live.`,
        );
      }
      return { ok: true };
    } catch (error) {
      const rawDetail = (error as Error).message || 'Unable to join scouting room.';
      const detail = (
        !createIfMissing
        && resolvedRoomKey
        && rawDetail.toLowerCase().includes('not found')
      )
        ? `Room ${resolvedRoomKey} was not found. Check the key/QR from your leader. Leave key blank only when creating a new room.`
        : roomRequestErrorMessage(rawDetail, createIfMissing ? 'Creating the scouting room' : 'Joining the scouting room');
      setRoomErrorText(detail);
      return { ok: false, error: detail };
    }
  }

  function leaveRoom() {
    const leavingRoomKey = normalizeRoomKey(activeRoom?.room_key || roomKeyInput || '');
    setEntries((current) => stripEntriesForRoom(current, leavingRoomKey));
    if (leavingRoomKey) clearStoredRoomAccessToken(leavingRoomKey);
    try {
      window.sessionStorage.removeItem(SCOUTING_ROOM_KEY_STORAGE);
    } catch {
      // ignore storage failures
    }
    roomReconnectAttemptsRef.current = 0;
    if (roomReconnectTimerRef.current !== null) {
      window.clearTimeout(roomReconnectTimerRef.current);
      roomReconnectTimerRef.current = null;
    }
    setRoomKeyInput('');
    setActiveRoom(null);
    setActiveRoomAccess(null);
    setRoomPresence([]);
    setRoomAssignments([]);
    setRoomConnectionState('disconnected');
    setLastSavedEntryId('');
    setHistoryMineOnly(true);
    setRoomErrorText('');
    setRoomQrOpen(false);
    setStatusText('Left room.');
  }

  async function copyRoomKey() {
    if (!activeRoom?.room_key) {
      setRoomErrorText('No active room key to copy.');
      return;
    }
    const copied = await copyToClipboard(activeRoom.room_key);
    if (copied) {
      setStatusText(`Copied room key.`);
      setRoomErrorText('');
    } else {
      setRoomErrorText('Unable to copy room key.');
    }
  }

  async function kickRoomMember(targetScoutProfile: string) {
    const activeRoomKey = normalizeRoomKey(activeRoom?.room_key || '');
    if (!activeRoomKey) {
      setRoomErrorText('Join a room before kicking members.');
      return;
    }
    if (!hasRoomOwnerAuthority) {
      setRoomErrorText('Only room leaders can kick members.');
      return;
    }
    const targetProfile = normalizeScoutProfile(targetScoutProfile);
    if (!targetProfile) return;
    if (targetProfile.toLowerCase() === normalizeScoutProfile(scoutProfile).toLowerCase()) {
      setRoomErrorText('Use Leave Room to remove yourself.');
      return;
    }
    const roomAccessToken = await ensureRoomAccessToken(activeRoomKey);
    if (!roomAccessToken) {
      setRoomErrorText('Room access token missing or expired. Re-join room to continue.');
      return;
    }
    setRoomKickPendingProfile(targetProfile);
    try {
      const response = await kickScoutingRoomMember(activeRoomKey, {
        scout_profile: targetProfile,
        room_access_token: roomAccessToken,
      });
      if (response.kicked) {
        setStatusText(`Removed ${response.target_scout_profile} from room.`);
      } else {
        setStatusText(`${targetProfile} is not currently connected.`);
      }
      setRoomErrorText('');
    } catch (error) {
      setRoomErrorText((error as Error).message || 'Unable to remove member from room.');
    } finally {
      setRoomKickPendingProfile('');
    }
  }

  async function promoteSecondaryLeader(targetScoutProfile: string) {
    const activeRoomKey = normalizeRoomKey(activeRoom?.room_key || '');
    if (!activeRoomKey) {
      setRoomErrorText('Join a room before promoting leaders.');
      return;
    }
    if (!hasRoomOwnerAuthority) {
      setRoomErrorText('Only room leaders can promote secondary leaders.');
      return;
    }
    const targetProfile = normalizeScoutProfile(targetScoutProfile);
    if (!targetProfile) return;
    const roomAccessToken = await ensureRoomAccessToken(activeRoomKey);
    if (!roomAccessToken) {
      setRoomErrorText('Room access token missing or expired. Re-join room to continue.');
      return;
    }
    setRoomPromotePendingProfile(targetProfile);
    try {
      const response = await addScoutingRoomSecondaryLeader(activeRoomKey, {
        scout_profile: targetProfile,
        room_access_token: roomAccessToken,
      });
      setActiveRoom((current) => (
        current
          ? {
            ...current,
            secondary_leader_scout_profiles: Array.isArray(response.secondary_leader_scout_profiles)
              ? response.secondary_leader_scout_profiles
              : current.secondary_leader_scout_profiles,
          }
          : current
      ));
      setRoomErrorText('');
      setStatusText(`Promoted ${response.target_scout_profile} to secondary leader.`);
    } catch (error) {
      setRoomErrorText((error as Error).message || 'Unable to promote secondary leader.');
    } finally {
      setRoomPromotePendingProfile('');
    }
  }

  async function demoteSecondaryLeader(targetScoutProfile: string) {
    const activeRoomKey = normalizeRoomKey(activeRoom?.room_key || '');
    if (!activeRoomKey) {
      setRoomErrorText('Join a room before removing leaders.');
      return;
    }
    if (!hasRoomOwnerAuthority) {
      setRoomErrorText('Only room leaders can remove secondary leaders.');
      return;
    }
    const targetProfile = normalizeScoutProfile(targetScoutProfile);
    if (!targetProfile) return;
    const roomAccessToken = await ensureRoomAccessToken(activeRoomKey);
    if (!roomAccessToken) {
      setRoomErrorText('Room access token missing or expired. Re-join room to continue.');
      return;
    }
    setRoomDemotePendingProfile(targetProfile);
    try {
      const response = await removeScoutingRoomSecondaryLeader(activeRoomKey, {
        scout_profile: targetProfile,
        room_access_token: roomAccessToken,
      });
      setActiveRoom((current) => (
        current
          ? {
            ...current,
            secondary_leader_scout_profiles: Array.isArray(response.secondary_leader_scout_profiles)
              ? response.secondary_leader_scout_profiles
              : current.secondary_leader_scout_profiles,
          }
          : current
      ));
      setRoomErrorText('');
      setStatusText(
        response.removed
          ? `Removed ${response.target_scout_profile} from secondary leaders.`
          : `${response.target_scout_profile} is not a secondary leader.`,
      );
    } catch (error) {
      setRoomErrorText((error as Error).message || 'Unable to remove secondary leader.');
    } finally {
      setRoomDemotePendingProfile('');
    }
  }

  async function resolveApiSnapshot(teamKey: string, eventKey: string): Promise<ApiTeamSnapshot | null> {
    const cacheKey = `${eventKey}|${teamKey}`;
    if (Object.prototype.hasOwnProperty.call(apiSnapshotCache, cacheKey)) {
      return apiSnapshotCache[cacheKey] ?? null;
    }
    try {
      const intel = await getTeamIntel(teamKey, {
        event_key: eventKey,
        preferred_year: CURRENT_SEASON_YEAR,
        fallback_year: FALLBACK_SEASON_YEAR,
        include_tba: true,
        include_statbotics: false,
        allow_season_fallback: true,
        auto_heal_ratings: true,
      });
      const snapshot = apiSnapshotFromIntel(intel, eventKey);
      setApiSnapshotCache((current) => ({
        ...current,
        [cacheKey]: snapshot,
      }));
      return snapshot;
    } catch {
      setApiSnapshotCache((current) => ({
        ...current,
        [cacheKey]: null,
      }));
      return null;
    }
  }

  async function saveScoutingEntry() {
    if (!selectedEventKey || !selectedMatch || !selectedTeam) {
      setErrorText('Pick event, match, and team before saving.');
      return;
    }
    if (!hasScoutProfile) {
      setErrorText('Enter your scout name before saving scouting entries.');
      return;
    }
    setSavingEntry(true);
    const savedAt = Date.now();
    try {
      let entrySource: SavedScoutingEntry['entry_source'] = 'manual';
      let autoScoutMeta: SavedScoutingEntry['auto_scout_meta'] = null;
      let fieldOverrides: SavedScoutingEntry['field_overrides'] = null;
      if (entryCaptureMode === 'auto') {
        const approved = await autoScout.prepareReviewedAutoSave();
        if (!approved) {
          setErrorText('Generate and review an auto draft before saving.');
          return;
        }
        entrySource = 'reviewed_auto';
        autoScoutMeta = approved.autoScoutMeta;
        fieldOverrides = approved.fieldOverrides;
      }
      const manualRating = liveManualRating;
      const overallRating = overallScoutRating(pointsSummary, manualRating, driverScore);
      const apiSnapshot = await resolveApiSnapshot(selectedTeam.team_key, selectedEventKey);
      const baseline = eventApiBaselineByTeam[selectedTeam.team_key] || null;
      const apiRating = scoutingApiRating(manualRating, apiSnapshot, baseline);
      const entry = buildSavedScoutingEntry({
        id: `${savedAt}-${Math.random().toString(36).slice(2, 8)}`,
        saved_at_ms: savedAt,
        scout_profile: scoutProfile,
        room_key: activeRoom?.room_key ? normalizeRoomKey(activeRoom.room_key) : null,
        mode: scoutingMode,
        event_key: selectedEventKey,
        match_key: selectedMatch.match_key.toLowerCase(),
        match_display: selectedMatch.display_name,
        team_key: selectedTeam.team_key,
        team_label: selectedTeam.team_label,
        alliance: selectedTeam.alliance,
        station: selectedTeam.station,
        points: pointsSummary,
        rp: rpState,
        form,
        driver_competency: driverScore,
        manual_rating: manualRating,
        overall_scout_rating: overallRating,
        scouting_api_rating: apiRating,
        api_snapshot: apiSnapshot,
        notes: normalizeScoutingNotes(scoutNotes),
        entry_source: entrySource,
        auto_scout_meta: autoScoutMeta,
        field_overrides: fieldOverrides,
      });
      setEntries((current) => [entry, ...current].slice(0, 600));
      setLastSavedAt(savedAt);
      setLastSavedEntryId(entry.id);
      setErrorText('');
      hapticSuccess();
      const _apiText = apiRating ? `${apiRating.score_0_100.toFixed(1)}` : 'N/A';
      void _apiText;
      let syncSuffix = 'Local save only (no room active).';
      if (activeRoom?.room_key) {
        let roomAccessToken = await ensureRoomAccessToken(activeRoom.room_key);
        if (!roomAccessToken) {
          setRoomErrorText('Room access token missing or expired. Re-join room before syncing entries.');
          syncSuffix = 'Room sync skipped (room access token missing).';
        } else {
          try {
            const sync = await saveScoutingRoomEntry(activeRoom.room_key, {
              entry,
              scout_profile: scoutProfile,
              client_entry_id: entry.id,
              room_access_token: roomAccessToken,
              timeoutMs: 30000,
            });
            const syncedEntry = normalizeEntry(sync.entry.entry);
            if (syncedEntry) {
              const syncedWithRoom = {
                ...syncedEntry,
                room_key: normalizeRoomKey(sync.entry.room_key || activeRoom.room_key) || syncedEntry.room_key || null,
              } satisfies SavedScoutingEntry;
              setEntries((current) => mergeEntry(current, syncedWithRoom));
            }
            syncSuffix = `Synced to room ${activeRoom.room_key}.`;
            setRoomErrorText('');
          } catch (error) {
            const detail = (error as Error).message || 'Room sync failed.';
            if (isRoomAccessAuthError(detail)) {
              roomAccessToken = await refreshRoomAccessSession(activeRoom.room_key, { silent: true });
              if (roomAccessToken) {
                try {
                  const sync = await saveScoutingRoomEntry(activeRoom.room_key, {
                    entry,
                    scout_profile: scoutProfile,
                    client_entry_id: entry.id,
                    room_access_token: roomAccessToken,
                    timeoutMs: 30000,
                  });
                  const syncedEntry = normalizeEntry(sync.entry.entry);
                  if (syncedEntry) {
                    const syncedWithRoom = {
                      ...syncedEntry,
                      room_key: normalizeRoomKey(sync.entry.room_key || activeRoom.room_key) || syncedEntry.room_key || null,
                    } satisfies SavedScoutingEntry;
                    setEntries((current) => mergeEntry(current, syncedWithRoom));
                  }
                  syncSuffix = `Synced to room ${activeRoom.room_key}.`;
                  setRoomErrorText('');
                } catch (retryError) {
                  const retryDetail = roomRequestErrorMessage(
                    (retryError as Error).message || detail,
                    'Saving to the scouting room',
                  );
                  setRoomErrorText(retryDetail);
                  syncSuffix = `Room sync failed (${retryDetail}).`;
                }
              } else {
                const nextDetail = roomRequestErrorMessage(detail, 'Saving to the scouting room');
                setRoomErrorText(nextDetail);
                syncSuffix = `Room sync failed (${nextDetail}).`;
              }
            } else {
              const nextDetail = roomRequestErrorMessage(detail, 'Saving to the scouting room');
              setRoomErrorText(nextDetail);
              syncSuffix = `Room sync failed (${nextDetail}).`;
            }
          }
        }
      }
      setStatusText(
        `Saved ${selectedTeam.team_key.toUpperCase()} · ${overallRating.score_0_100}/100${entrySource === 'reviewed_auto' ? ' · reviewed auto draft' : ''}. ${syncSuffix}`,
      );
      if (scoutingMode === 'rapid') {
        setForm(EMPTY_FORM);
        setRpState(EMPTY_RP);
        setTimerSec(MATCH_DURATION_SEC);
        setTimerRunning(false);
      }
    } finally {
      setSavingEntry(false);
    }
  }

  function clearAllEntries() {
    const confirmed = window.confirm('Clear all local scouting entries on this device?');
    if (!confirmed) return;
    setEntries([]);
    setLastSavedEntryId('');
    setStatusText('Cleared entries.');
  }


  function exportEntriesHtml() {
    const filtered = historyMineOnly
      ? entries.filter((e) => normalizeScoutProfile(e.scout_profile) === scoutProfile)
      : entries;
    if (filtered.length === 0) return;

    const esc = (v: unknown) =>
      String(v ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

    const stars = (n: number | null) =>
      n === null
        ? '<span style="color:#64748b;font-size:0.8em">N/A</span>'
        : '★'.repeat(Math.max(0, Math.min(5, Math.round(n)))) + '☆'.repeat(Math.max(0, 5 - Math.min(5, Math.round(n))));

    const badge = (val: number, max: number) => {
      const pct = max > 0 ? val / max : 0;
      const color = pct >= 0.7 ? '#2bd171' : pct >= 0.4 ? '#f59e0b' : '#ef4444';
      return `<span style="display:inline-block;min-width:36px;padding:2px 7px;border-radius:99px;background:${color}22;color:${color};font-weight:700;font-size:0.85em">${val}</span>`;
    };

    const rows = filtered
      .slice()
      .sort((a, b) => a.match_key.localeCompare(b.match_key))
      .map((e) => {
        const allianceColor = e.alliance === 'red' ? '#ef4444' : '#3b82f6';
        const allianceBg = e.alliance === 'red' ? '#ef444418' : '#3b82f618';
        return `<tr>
          <td style="white-space:nowrap"><strong style="color:#e2e8f0">${esc(e.match_display)}</strong></td>
          <td>
            <div style="font-weight:700;color:${allianceColor}">${esc(e.team_label)}</div>
            <div style="font-size:0.78em;background:${allianceBg};color:${allianceColor};display:inline-block;padding:1px 6px;border-radius:4px;margin-top:2px">${esc(e.alliance.toUpperCase())} ${esc(e.station ?? '')}</div>
          </td>
          <td style="text-align:center">${badge(e.points.auto, 60)}</td>
          <td style="text-align:center">${badge(e.points.teleop, 150)}</td>
          <td style="text-align:center">${badge(e.points.endgame, 30)}</td>
          <td style="text-align:center"><strong style="color:#e2e8f0;font-size:1.05em">${badge(e.points.total, 200)}</strong></td>
          <td style="text-align:center;font-size:0.9em">${e.form.auto_mobility ? '<span style="color:#2bd171">✓ Mob</span>' : '<span style="color:#64748b">–</span>'} ${e.form.auto_scored > 0 ? `<span style="color:#2bd171">+${e.form.auto_scored}</span>` : ''}</td>
          <td style="text-align:center">${esc(e.form.teleop_scored)} <span style="color:#64748b;font-size:0.8em">/ ${esc(e.form.teleop_scored + e.form.teleop_missed)} att</span></td>
          <td style="text-align:center"><span style="color:#e2e8f0">${esc(e.form.endgame_mode.replace(/_/g, ' '))}</span></td>
          <td style="text-align:center" title="Offense / Defense / Field Awareness">${stars(e.form.offense_level_1_5)}<br><small style="color:#94a3b8">OFF</small></td>
          <td style="text-align:center">${badge(Math.round(e.overall_scout_rating.score_0_100), 100)}</td>
          <td style="max-width:200px;font-size:0.85em;color:#cbd5e1">${esc(e.notes) || '<span style="color:#475569">—</span>'}</td>
          <td style="font-size:0.78em;color:#64748b;white-space:nowrap">${esc(e.scout_profile)}</td>
        </tr>`;
      })
      .join('\n');

    const eventLabel = selectedEventKey ? selectedEventKey.toUpperCase() : 'All Events';
    const exportedAt = new Date().toLocaleString();

    const html = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Scouting Report — ${esc(eventLabel)}</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: #0d1520;
    color: #94a3b8;
    font-size: 14px;
    padding: 16px;
  }
  h1 { color: #e2e8f0; font-size: 1.3em; margin-bottom: 4px; }
  .meta { color: #475569; font-size: 0.82em; margin-bottom: 20px; }
  .meta strong { color: #64748b; }
  .table-wrap { overflow-x: auto; border-radius: 12px; border: 1px solid #1e2d3d; }
  table { border-collapse: collapse; width: 100%; min-width: 900px; }
  thead th {
    background: #111d2b;
    color: #64748b;
    font-size: 0.75em;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    padding: 10px 12px;
    text-align: left;
    white-space: nowrap;
    position: sticky;
    top: 0;
    border-bottom: 1px solid #1e2d3d;
  }
  tbody tr { border-bottom: 1px solid #131f2b; }
  tbody tr:last-child { border-bottom: none; }
  tbody tr:nth-child(even) { background: #0f1a26; }
  tbody tr:nth-child(odd)  { background: #0d1520; }
  tbody tr:hover { background: #162233; }
  td { padding: 10px 12px; vertical-align: middle; }
  .summary-bar {
    display: flex;
    gap: 20px;
    flex-wrap: wrap;
    margin-bottom: 20px;
    padding: 14px 18px;
    background: #111d2b;
    border-radius: 10px;
    border: 1px solid #1e2d3d;
  }
  .kpi { display: flex; flex-direction: column; gap: 2px; }
  .kpi-val { font-size: 1.5em; font-weight: 700; color: #e2e8f0; }
  .kpi-label { font-size: 0.75em; color: #475569; text-transform: uppercase; letter-spacing: 0.05em; }
</style>
</head>
<body>
  <h1>Scouting Report — ${esc(eventLabel)}</h1>
  <p class="meta">Exported <strong>${esc(exportedAt)}</strong> &nbsp;·&nbsp; <strong>${filtered.length}</strong> entries${historyMineOnly && scoutProfile ? ` &nbsp;·&nbsp; Scout: <strong>${esc(scoutProfile)}</strong>` : ''}</p>

  <div class="summary-bar">
    <div class="kpi"><span class="kpi-val">${filtered.length}</span><span class="kpi-label">Entries</span></div>
    <div class="kpi"><span class="kpi-val">${Math.round(filtered.reduce((s, e) => s + e.points.total, 0) / (filtered.length || 1))}</span><span class="kpi-label">Avg Total Pts</span></div>
    <div class="kpi"><span class="kpi-val">${Math.round(filtered.reduce((s, e) => s + e.points.auto, 0) / (filtered.length || 1))}</span><span class="kpi-label">Avg Auto</span></div>
    <div class="kpi"><span class="kpi-val">${Math.round(filtered.reduce((s, e) => s + e.points.teleop, 0) / (filtered.length || 1))}</span><span class="kpi-label">Avg Teleop</span></div>
    <div class="kpi"><span class="kpi-val">${new Set(filtered.map((e) => e.team_key)).size}</span><span class="kpi-label">Teams</span></div>
  </div>

  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>Match</th><th>Team</th><th>Auto Pts</th><th>Teleop Pts</th><th>End Pts</th><th>Total</th>
          <th>Auto</th><th>Teleop Shots</th><th>Endgame</th><th>Offense</th><th>Rating</th><th>Notes</th><th>Scout</th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
  </div>
</body>
</html>`;

    const blob = new Blob([html], { type: 'text/html;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = entryFileName(selectedEventKey || 'all-events', historyMineOnly ? scoutProfile : 'all').replace('.json', '.html');
    a.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 0);
  }

  function exportEntriesCsv() {
    const filtered = historyMineOnly
      ? entries.filter((e) => normalizeScoutProfile(e.scout_profile) === scoutProfile)
      : entries;
    if (filtered.length === 0) return;

    const byTeam = new Map<string, typeof filtered>();
    for (const entry of filtered) {
      const list = byTeam.get(entry.team_key) ?? [];
      list.push(entry);
      byTeam.set(entry.team_key, list);
    }

    const avg = (arr: number[]) => arr.length ? Math.round(arr.reduce((s, v) => s + v, 0) / arr.length) : '';
    const avgLevel = (arr: (number | null)[]) => {
      const valid = arr.filter((v): v is number => v !== null);
      return valid.length ? (valid.reduce((s, v) => s + v, 0) / valid.length).toFixed(1) : 'N/A';
    };

    const headers = [
      'Team', 'Team Key', 'Matches Scouted',
      'Avg Total Pts', 'Avg Auto Pts', 'Avg Teleop Pts', 'Avg Endgame Pts',
      'Avg Overall Score', 'Avg Driver Score', 'Avg Manual Score',
      'Avg Offense (1-5)', 'Avg Defense (1-5)', 'Avg Field Awareness (1-5)',
      'Avg Decision Quality (1-5)', 'Avg Communication (1-5)',
      'Avg Anti-Defense (1-5)', 'Avg Escape (1-5)',
      'Matches', 'All Notes',
    ];

    const rows = Array.from(byTeam.entries())
      .sort((a, b) => a[0].localeCompare(b[0]))
      .map(([teamKey, teamEntries]) => {
        const sorted = teamEntries.slice().sort((a, b) => a.match_key.localeCompare(b.match_key));
        const n = sorted.length;
        const matchList = sorted.map((e) => e.match_display).join(', ');
        const notes = sorted.map((e) => e.notes).filter(Boolean).join(' | ');
        return [
          sorted[0].team_label,
          teamKey,
          n,
          avg(sorted.map((e) => e.points.total)),
          avg(sorted.map((e) => e.points.auto)),
          avg(sorted.map((e) => e.points.teleop)),
          avg(sorted.map((e) => e.points.endgame)),
          avg(sorted.map((e) => e.overall_scout_rating.score_0_100)),
          avg(sorted.map((e) => e.driver_competency.score_0_100)),
          avg(sorted.map((e) => e.manual_rating.score_0_100)),
          avgLevel(sorted.map((e) => e.form.offense_level_1_5)),
          avgLevel(sorted.map((e) => e.form.defense_level_1_5)),
          avgLevel(sorted.map((e) => e.form.field_awareness_1_5)),
          avgLevel(sorted.map((e) => e.form.decision_quality_1_5)),
          avgLevel(sorted.map((e) => e.form.communication_1_5)),
          avgLevel(sorted.map((e) => e.form.anti_defense_level_1_5)),
          avgLevel(sorted.map((e) => e.form.escape_level_1_5)),
          matchList,
          notes,
        ];
      });

    const filename = entryFileName(selectedEventKey || 'all-events', historyMineOnly ? scoutProfile : 'all').replace('.json', '.csv');
    downloadCsv(filename, headers, rows);
  }

  function reconnectRoomSocket() {
    if (!activeRoom?.room_key) {
      setRoomErrorText('Join a room before reconnecting.');
      return;
    }
    roomReconnectAttemptsRef.current = 0;
    if (roomReconnectTimerRef.current !== null) {
      window.clearTimeout(roomReconnectTimerRef.current);
      roomReconnectTimerRef.current = null;
    }
    setRoomConnectionState('connecting');
    setRoomSocketNonce((current) => current + 1);
    setStatusText(`Reconnecting room...`);
  }

  function focusSidebarSection(section: SidebarSection) {
    setSidebarSection(section);
    window.requestAnimationFrame(() => {
      const target =
        section === 'setup'
          ? setupSectionRef.current
          : section === 'room'
            ? roomSectionRef.current
            : dataSectionRef.current;
      target?.scrollIntoView({ behavior: 'smooth', block: isMobileLayout ? 'start' : 'nearest' });
    });
  }

  const saveReady =
    Boolean(selectedEventKey)
    && Boolean(selectedMatchKey)
    && Boolean(selectedTeamKey)
    && hasScoutProfile;
  const roomMemberCount = roomPresence.reduce((sum, member) => sum + member.connections, 0);
  const roomConnectionLabel =
    roomConnectionState === 'connected'
      ? 'Connected'
      : roomConnectionState === 'connecting'
        ? 'Connecting'
        : 'Offline';
  const setupSectionSummary = selectedTeamKey
    ? `${selectedMatch?.display_name || 'Match selected'} · ${selectedTeamKey.toUpperCase()}`
    : selectedMatch?.display_name
      ? `${selectedMatch.display_name} selected. Pick the robot to scout.`
      : selectedEventKey
        ? `${selectedEventKey} loaded. Choose a match to continue.`
        : 'Pick an event, match, and robot before the match starts.';
  const roomSectionSummary = activeRoom?.room_key
    ? `${roomConnectionLabel} · ${roomMemberCount} active member${roomMemberCount === 1 ? '' : 's'}`
    : 'Not in a room. Entries stay local until you join one.';
  const dataSectionSummary = entries.length > 0
    ? `${entries.length} saved entr${entries.length === 1 ? 'y' : 'ies'} · Last save ${relativeFromTimestamp(lastSavedAt)}`
    : saveReady
      ? 'Ready to save the first scouting entry.'
      : 'Set scout name, event, match, and team to enable save.';
  const activeSectionTitle =
    sidebarSection === 'setup'
      ? 'Setup'
      : sidebarSection === 'room'
        ? 'Room'
        : 'Data';
  const activeSectionSummary =
    sidebarSection === 'setup'
      ? setupSectionSummary
      : sidebarSection === 'room'
        ? roomSectionSummary
        : dataSectionSummary;
  const showSetupSidebarSection = !isMobileLayout || sidebarSection === 'setup';
  const showRoomSidebarSection = !isMobileLayout || sidebarSection === 'room';
  const showDataSidebarSection = !isMobileLayout || sidebarSection === 'data';
  const autoScoutEnabled = entryCaptureMode === 'auto';
  const autoScoutInsightRows = Object.entries(autoScout.derivedInsights || {});
  const setupHeaderCollapseEligible = !isMobileLayout || mobileFinderOpen;
  const setupHeaderCollapsed = setupHeaderCollapseEligible && (scoutingTopHidden || setupHeaderManuallyCollapsed);
  const scoutingSidebarSideCollapsed = !isMobileLayout && setupHeaderManuallyCollapsed;

  function renderAutoScoutBadge(fieldName: string) {
    if (!autoScoutEnabled) return null;
    const badge = autoScout.getFieldBadge(fieldName);
    if (!badge) return null;
    const canOpenEvidence = badge.evidenceCount > 0;
    return (
      <AutoScoutFieldBadge
        label={badge.label}
        tone={badge.tone}
        confidence={badge.confidence}
        evidenceCount={badge.evidenceCount}
        onClick={canOpenEvidence ? () => { void autoScout.openEvidence(fieldName); } : undefined}
      />
    );
  }

  const captureAutoCard = showCaptureAutoCard ? (
    <SurfaceCard title="Auto" subtitle="Autonomous scoring.">
      <div className="scout-control-card">
        {countersFor('auto').map((field) => (
          <CounterInput
            key={field.key}
            label={field.label}
            value={form[field.key]}
            onChange={(next) => updateCounter(field.key, next)}
            step={field.step}
            badge={renderAutoScoutBadge(field.key)}
          />
        ))}
        <button
          type="button"
          className={`scout-toggle-btn ${form.auto_mobility ? 'active' : ''}`.trim()}
          onClick={() => setForm((current) => ({ ...current, auto_mobility: !current.auto_mobility }))}
        >
          <span>Mobility {form.auto_mobility ? 'Done' : 'Not done'}</span>
          {renderAutoScoutBadge('auto_mobility')}
        </button>
        {scalesFor('auto').map((field) => (
          <ScaleInput
            key={field.key}
            label={field.label}
            value={form[field.key]}
            onChange={(next) => setForm((current) => ({ ...current, [field.key]: next }))}
            badge={renderAutoScoutBadge(field.key)}
          />
        ))}
      </div>
    </SurfaceCard>
  ) : null;

  const captureEndgameCard = showCaptureEndgameCard ? (
    <SurfaceCard title="Endgame" subtitle="Endgame result and pressure context.">
      <div className="scout-control-card">
        <label className="center-label scout-inline-label" htmlFor="scout-endgame-select">
          <span>Endgame result</span>
          {renderAutoScoutBadge('endgame_mode')}
        </label>
        <select
          id="scout-endgame-select"
          className="center-input"
          value={form.endgame_mode}
          onChange={(event) =>
            setForm((current) => ({
              ...current,
              endgame_mode: event.target.value as EndgameMode,
            }))
          }
        >
          <option value="none">{ENDGAME_LABELS.none}</option>
          <option value="kept_scoring">{ENDGAME_LABELS.kept_scoring}</option>
          <option value="parked">{ENDGAME_LABELS.parked}</option>
          <option value="climb_level_1">{ENDGAME_LABELS.climb_level_1}</option>
          <option value="climb_level_2">{ENDGAME_LABELS.climb_level_2}</option>
          <option value="climb_level_3">{ENDGAME_LABELS.climb_level_3}</option>
        </select>
        <button
          type="button"
          className={`scout-toggle-btn ${form.climbed_under_pressure ? 'active' : ''}`.trim()}
          onClick={() => setForm((current) => ({ ...current, climbed_under_pressure: !current.climbed_under_pressure }))}
        >
          <span>Climbed while defended {form.climbed_under_pressure ? 'Yes' : 'No'}</span>
          {renderAutoScoutBadge('climbed_under_pressure')}
        </button>
        <button
          type="button"
          className={`scout-toggle-btn ${form.protected_zone_risk ? 'active' : ''}`.trim()}
          onClick={() => setForm((current) => ({ ...current, protected_zone_risk: !current.protected_zone_risk }))}
        >
          <span>Protected-zone risk {form.protected_zone_risk ? 'High' : 'Low'}</span>
          {renderAutoScoutBadge('protected_zone_risk')}
        </button>
        <p className="scout-card-note">Current endgame points: {ENDGAME_POINTS[form.endgame_mode]}</p>
      </div>
    </SurfaceCard>
  ) : null;

  return (
    <>
    <PageViewBar items={SCOUTING_VIEWS} className="scouting-page-view-bar" collapseToMenuOnMobile />
    <div
      className={`center-layout center-layout-team scouting-layout-grid mobile-finder-layout ${isMobileLayout && mobileFinderOpen ? 'mobile-finder-open' : ''} ${isMobileLayout && mobileCompactMode ? 'scouting-mobile-compact' : ''} ${isMobileLayout && scoutingTopCondensed ? 'scouting-top-condensed' : ''} ${isMobileLayout && scoutingTopHidden ? 'scouting-top-hidden' : ''} ${scoutingSidebarSideCollapsed ? 'scouting-sidebar-side-collapsed' : ''}`.trim()}
    >
      {!isMobileLayout ? (
        <div
          ref={floatingTimerRef}
          className={`scout-mini-timer-float ${floatingTimerDragging ? 'dragging' : ''}`.trim()}
          style={floatingTimerStyle}
          aria-live="polite"
          aria-atomic="true"
          title={selectedMatch?.display_name || 'No selected match'}
        >
          <div
            className="scout-mini-timer-top scout-mini-timer-drag-handle"
            onPointerDown={handleFloatingTimerDragStart}
            onTouchStart={handleFloatingTimerTouchStart}
          >
            <strong className="scout-mini-timer-clock">{timerClockLabel}</strong>
            <span className={`center-chip timer ${timerPhaseState}`.trim()}>{timerPhase}</span>
          </div>
          <div className="scout-mini-timer-bottom">
            <span className={`scout-mini-timer-dot ${timerRunning ? 'live' : 'idle'}`.trim()} aria-hidden="true" />
            <small>{liveTimer.label}: {liveTimer.value}</small>
          </div>
          <div className="scout-mini-timer-controls" role="group" aria-label="Mini timer controls">
            <button
              type="button"
              className="scout-mini-timer-btn"
              aria-label={timerRunning ? 'Pause timer' : 'Start timer'}
              onClick={() => setTimerRunning((current) => !current)}
            >
              {timerRunning ? <PauseIcon size={12} /> : <PlayIcon size={12} />}
            </button>
            <button
              type="button"
              className="scout-mini-timer-btn"
              aria-label="Reset timer"
              onClick={() => {
                setTimerSec(MATCH_DURATION_SEC);
                setTimerRunning(false);
              }}
            >
              <ResetIcon size={12} />
            </button>
          </div>
        </div>
      ) : null}
      {isMobileLayout ? (
        <SegmentedTabs
          className="mobile-view-toggle"
          itemClassName="mobile-view-toggle-btn"
          ariaLabel="Scouting mobile view switch"
          value={mobileFinderOpen ? sidebarSection : 'board'}
          onChange={(next) => {
            if (next === 'board') {
              setMobileFinderOpen(false);
            } else {
              // Setup / Room / Data are sections of the setup finder. Merging
              // them into this mode toggle means mobile shows one bar, not the
              // old toggle + in-sidebar section nav stacked together.
              setMobileFinderOpen(true);
              focusSidebarSection(next);
            }
          }}
          items={[
            { value: 'setup', label: 'Setup', icon: <SettingsIcon className="icon-inline" /> },
            { value: 'room', label: 'Room', icon: <UsersIcon className="icon-inline" /> },
            { value: 'data', label: 'Data', icon: <SaveIcon className="icon-inline" /> },
            {
              value: 'board',
              label: 'Board',
              icon: <ClipboardIcon className="icon-inline" />,
              disabled: !selectedEventKey,
            },
          ]}
        />
      ) : null}
      <aside className={`center-sidebar ${scoutingSidebarSideCollapsed ? 'scout-sidebar-side-collapsed' : ''}`.trim()}>
        <div
          className={`finder-sidebar-header scout-sidebar-hub ${setupHeaderCollapsed ? 'collapsed' : ''}`.trim()}
          aria-label="Scouting session controls"
        >
          {!isMobileLayout ? (
            <div className="scout-sidebar-overview">
              <div className="scout-sidebar-overview-copy">
                <span className="scout-sidebar-overview-kicker">Scouting workspace</span>
                <strong>{activeSectionTitle}</strong>
                <p>{activeSectionSummary}</p>
              </div>
              <div className="scout-sidebar-overview-actions">
                <span className={`scout-sidebar-overview-state ${saveReady ? 'ready' : 'pending'}`.trim()}>
                  {saveReady ? 'Ready to save' : 'Setup required'}
                </span>
                <button
                  type="button"
                  className="center-btn"
                  onClick={() => {
                    focusSidebarSection('data');
                    void saveScoutingEntry();
                  }}
                  disabled={!saveReady || savingEntry}
                >
                  {savingEntry ? 'Saving...' : <><SaveIcon className="icon-inline" /> Save Entry</>}
                </button>
              </div>
            </div>
          ) : null}

          <button
            type="button"
            className={`scout-sidebar-collapse-btn ${scoutingSidebarSideCollapsed ? 'icon-only' : ''}`.trim()}
            onClick={() => {
              if (setupHeaderCollapsed) {
                setScoutingTopHidden(false);
                setSetupHeaderManuallyCollapsed(false);
                return;
              }
              setSetupHeaderManuallyCollapsed(true);
            }}
            aria-expanded={!setupHeaderCollapsed}
            aria-label={setupHeaderCollapsed ? 'Expand setup controls' : 'Collapse setup controls'}
          >
            {setupHeaderCollapsed ? <ChevronDownIcon className="icon-inline" /> : <ChevronUpIcon className="icon-inline" />}
            <span>{setupHeaderCollapsed ? 'Expand' : 'Collapse'}</span>
          </button>

          <SegmentedTabs
            className={`scout-sidebar-nav ${setupHeaderCollapsed ? 'collapsed' : ''}`.trim()}
            itemClassName="scout-sidebar-nav-pill"
            ariaLabel="Scouting sidebar section"
            value={sidebarSection}
            onChange={focusSidebarSection}
            items={[
              { value: 'setup', label: 'Setup', icon: <SettingsIcon className="icon-inline" />, panelId: 'scouting-sidebar-setup' },
              { value: 'room', label: 'Room', icon: <UsersIcon className="icon-inline" />, panelId: 'scouting-sidebar-room' },
              { value: 'data', label: 'Data', icon: <SaveIcon className="icon-inline" />, panelId: 'scouting-sidebar-data' },
            ]}
          />
        </div>

        <div className={`finder-sidebar-sections ${scoutingSidebarSideCollapsed ? 'collapsed' : ''}`.trim()}>
        {showSetupSidebarSection ? (
        <div
          id="scouting-sidebar-setup"
          ref={setupSectionRef}
          className={`scout-sidebar-section ${sidebarSection === 'setup' ? 'active' : ''}`.trim()}
          onFocusCapture={() => setSidebarSection('setup')}
        >
          {!isMobileLayout ? (
            <div className="scout-sidebar-section-head">
              <span className="scout-sidebar-section-label">Setup</span>
              <span className="scout-sidebar-section-note">{setupSectionSummary}</span>
            </div>
          ) : null}
        <SurfaceCard title="Scouting Mode" subtitle="Tap-first scouting with direct number entry." collapsible>
          <SegmentedTabs
            className="center-tabs"
            itemClassName="center-tab-btn"
            ariaLabel="Scouting mode"
            value={scoutingMode}
            onChange={setScoutingMode}
            items={[
              { value: 'match', label: 'Match Scout', icon: <ClipboardCheckIcon className="icon-inline" /> },
              { value: 'rapid', label: 'Rapid Tap', icon: <ZapIcon className="icon-inline" /> },
            ]}
          />
          {!isMobileLayout ? (
            <p className="center-callout muted">
              Rapid Tap resets inputs after each save; Match Scout keeps values.
            </p>
          ) : null}

          <SegmentedTabs
            className="center-tabs"
            itemClassName="center-tab-btn"
            ariaLabel="Entry capture mode"
            value={entryCaptureMode}
            onChange={setEntryCaptureMode}
            items={[
              { value: 'manual', label: 'Manual', icon: <PenIcon className="icon-inline" /> },
              { value: 'auto', label: 'Auto Draft', icon: <RobotIcon className="icon-inline" />, disabled: !selectedEventKey || !selectedMatchKey || !selectedTeamKey },
            ]}
          />
          {!isMobileLayout ? (
            <p className="center-callout muted">
              Auto Draft prefills supported objective fields with evidence, then routes the reviewed result through the normal save flow.
            </p>
          ) : null}

          <label className="center-label" htmlFor="scout-event-select">
            Event
          </label>
          <EventPicker
            value={selectedEventKey}
            onSelect={(key) => {
              const next = normalizeEventKeyInput(key);
              setEventInput(next);
              setSelectedEventKey(next);
              setEventFetchCtr((c) => c + 1);
            }}
            inputValue={eventInput}
            onInputChange={(v) => setEventInput(normalizeEventKeyInput(v))}
            onSubmit={() => {
              const next = normalizeEventKeyInput(eventInput);
              setSelectedEventKey(next);
              setEventFetchCtr((c) => c + 1);
            }}
            disabled={!eventInput.trim() && false}
            placeholder={'Search events — try "houston district" or "2026txhou"'}
          />

          <label className="center-label" htmlFor="scout-match-select">
            Match
          </label>
          <select
            id="scout-match-select"
            className="center-input"
            value={selectedMatchKey}
            onChange={(event) => {
              const next = event.target.value.trim().toLowerCase();
              setSelectedMatchKey(next);
            }}
            disabled={scheduleRows.length === 0}
          >
            <option value="">{loadingSchedule ? 'Loading schedule...' : errorText ? 'Schedule unreachable' : 'Select match'}</option>
            {scheduleRows.map((match) => (
              <option key={`scout-match-${match.match_key}`} value={match.match_key.toLowerCase()}>
                {match.display_name} · {fmtDateShort(match.scheduled_time)}
              </option>
            ))}
          </select>
          {isMobileLayout ? (
            <div className="scout-match-quick-actions">
              <button
                type="button"
                className="scout-match-quick-btn"
                onClick={jumpToNextMatchTarget}
                disabled={!nextMatchTarget}
                title={nextMatchTarget ? `Next match: ${nextMatchTarget.match_display}` : 'No upcoming match'}
                aria-label="Jump to next match"
              >
                <ClockIcon size={16} />
              </button>
              <button
                type="button"
                className="scout-match-quick-btn"
                onClick={jumpToNextTeamTarget}
                disabled={!nextTeamTarget?.team_key}
                title={nextTeamTarget ? `Next team: ${nextTeamTarget.team_key.toUpperCase()}` : 'No next team'}
                aria-label="Jump to next team"
              >
                <UsersIcon size={16} />
              </button>
            </div>
          ) : (
            <div className="center-actions-row compact">
              <button
                type="button"
                className="center-btn ghost"
                onClick={jumpToNextMatchTarget}
                disabled={!nextMatchTarget}
                title={nextMatchTarget ? `Jump to ${nextMatchTarget.match_display}` : 'No upcoming match available'}
              >
                <ClockIcon className="icon-inline" /> Next Match
              </button>
              <button
                type="button"
                className="center-btn ghost"
                onClick={jumpToNextTeamTarget}
                disabled={!nextTeamTarget?.team_key}
                title={nextTeamTarget ? `Jump to ${nextTeamTarget.team_key.toUpperCase()}` : 'No next team available'}
              >
                <UsersIcon className="icon-inline" /> Next Team
              </button>
              <button
                type="button"
                className={`center-btn ghost ${mobileCompactMode ? 'active' : ''}`.trim()}
                onClick={() => setMobileCompactMode((current) => !current)}
              >
                {mobileCompactMode ? 'Dense Layout' : 'Relaxed Layout'}
              </button>
            </div>
          )}

          {myTeamKey ? (
            <div className="scout-opponent-section">
              <div className="scout-opponent-head">
                <strong>Upcoming Opponents for {myTeamKey.toUpperCase()}</strong>
                <small>
                  {upcomingOpponentOptions.length > 0
                    ? `${upcomingOpponentOptions.length} opponent${upcomingOpponentOptions.length !== 1 ? 's' : ''} from upcoming matches`
                    : 'No schedule loaded yet — opponents will appear when the schedule drops.'}
                </small>
              </div>
              {upcomingOpponentOptions.length > 0 ? (
                <div className="scout-opponent-grid">
                  {upcomingOpponentOptions.slice(0, 18).map((opponent) => (
                    <button
                      key={`scout-upcoming-opponent-${opponent.team_key}-${opponent.next_match_key}`}
                      type="button"
                      className={`scout-opponent-chip ${opponent.status}`.trim()}
                      onClick={() => {
                        setSelectedMatchKey(opponent.next_match_key);
                        setSelectedTeamKey(opponent.team_key);
                        setStatusText(`Loaded ${opponent.team_key.toUpperCase()} from ${opponent.next_match_display}.`);
                      }}
                    >
                      <strong>{opponent.team_key.toUpperCase()}</strong>
                      <small>{opponent.next_match_display}</small>
                    </button>
                  ))}
                </div>
              ) : null}
            </div>
          ) : null}

          <label className="center-label">Team</label>
          <div className="scout-team-grid">
            {teamOptions.map((team) => (
              <button
                key={`scout-team-${team.team_key}-${team.station || 'station'}`}
                type="button"
                className={`scout-team-chip ${selectedTeamKey === team.team_key ? 'active' : ''} ${team.alliance}`.trim()}
                onClick={() => {
                  setSelectedTeamKey(team.team_key);
                }}
              >
                <strong>{team.team_key.toUpperCase()}</strong>
                <small>{team.station || team.alliance.toUpperCase()}</small>
              </button>
            ))}
            {teamOptions.length === 0 ? <p className="center-callout muted">Select a match to pick a robot.</p> : null}
          </div>

          {!isMobileLayout ? (
            <>
              <div className="center-status-row compact scout-status-row">
                <span className="center-chip">{selectedEventKey || 'No event'}</span>
                <span className="center-chip">{selectedMatch?.display_name || 'No match'}</span>
                <span className="center-chip">{selectedTeamKey ? selectedTeamKey.toUpperCase() : 'No team'}</span>
                {myTeamKey ? <span className="center-chip">My Team: {myTeamKey.toUpperCase()}</span> : null}
              </div>

              <p className="center-callout muted">
                {loadingSuggested
                  ? 'Loading events...'
                  : scheduleName
                    ? `Schedule: ${scheduleName}.`
                    : 'No schedule loaded.'}
              </p>

              <p className="center-callout muted">
                {loadingEventIntel
                  ? 'Loading API baseline...'
                  : eventIntelError
                    ? `API baseline unavailable: ${eventIntelError}`
                    : `API baseline: ${Object.keys(eventApiBaselineByTeam).length} teams`}
              </p>

              <div className="center-actions-row">
                {selectedEventKey ? (
                  <Link className="center-btn ghost" to={`/events?event=${selectedEventKey}`}>
                    <CalendarIcon className="icon-inline" /> Event Center
                  </Link>
                ) : null}
                {selectedMatch?.match_key ? (
                  <Link
                    className="center-btn ghost"
                    to={`/match-center?event=${selectedEventKey}&match=${selectedMatch.match_key.toLowerCase()}`}
                  >
                    <ScoreboardIcon className="icon-inline" /> Match Center
                  </Link>
                ) : null}
              </div>
            </>
          ) : null}
        </SurfaceCard>
        </div>
        ) : null}

        {showRoomSidebarSection ? (
        <div
          id="scouting-sidebar-room"
          ref={roomSectionRef}
          className={`scout-sidebar-section ${sidebarSection === 'room' ? 'active' : ''}`.trim()}
          onFocusCapture={() => setSidebarSection('room')}
        >
          <div className="scout-sidebar-section-head">
            <span className="scout-sidebar-section-label">Room</span>
            <span className="scout-sidebar-section-note">{roomSectionSummary}</span>
          </div>
        <SurfaceCard title="Scout Profile" subtitle="Your identity and team.">
          <div className="scout-profile-grid">
            <label className="center-label" htmlFor="scout-profile-name">
              Scout Name
            </label>
            <input
              id="scout-profile-name"
              className="center-input"
              value={scoutProfile}
              onChange={(event) => setScoutProfile(normalizeScoutProfile(event.target.value))}
              placeholder="Required before saving (e.g., Jamal)"
            />
            {!hasScoutProfile ? (
              <p className="center-callout warning">Scout name required before saving or joining a room.</p>
            ) : null}
            <label className="center-label" htmlFor="scout-my-team-input">
              Your Team
            </label>
            <div className="center-input-row">
              <input
                id="scout-my-team-input"
                className="center-input"
                value={myTeamKey ? teamNumberFromTeamKey(myTeamKey)?.toString() ?? myTeamKey : ''}
                onChange={(event) => {
                  const raw = event.target.value.trim();
                  if (!raw) { setMyTeamKey(''); return; }
                  const normalized = normalizeTeamKeyInput(raw);
                  if (normalized) setMyTeamKey(normalized);
                  else setMyTeamKey(raw.toLowerCase());
                }}
                placeholder="Team number (e.g., 254)"
                inputMode="numeric"
              />
              {myTeamKey ? (
                <button type="button" className="center-btn ghost" onClick={() => setMyTeamKey('')}>
                  Clear
                </button>
              ) : null}
            </div>
            {eventTeamOptions.length > 0 ? (
              <div className="scout-profile-chip-row">
                {eventTeamOptions
                  .filter((team) => {
                    if (!myTeamKey) return true;
                    return team.team_key === myTeamKey;
                  })
                  .slice(0, myTeamKey ? 1 : 8)
                  .map((team) => (
                    <button
                      key={`my-team-${team.team_key}`}
                      type="button"
                      className={`scout-profile-chip ${team.team_key === myTeamKey ? 'active' : ''}`.trim()}
                      onClick={() => setMyTeamKey(team.team_key)}
                    >
                      {team.team_key.toUpperCase()}
                    </button>
                  ))}
              </div>
            ) : null}
            <p className="center-callout muted">
              {myTeamKey
                ? `Scouting as ${myTeamKey.toUpperCase()}. Opponents auto-populate with schedule.`
                : 'Set your team to surface upcoming opponents.'}
            </p>
          </div>
        </SurfaceCard>

        <SurfaceCard title="Scouting Room" subtitle="Share entries live with your team.">
          <div className="scout-profile-grid">
            <label className="center-label" htmlFor="scout-room-key">
              Room Key
            </label>
            <div className="center-input-row">
              <input
                id="scout-room-key"
                value={roomKeyInput}
                onChange={(event) => setRoomKeyInput(normalizeRoomKey(event.target.value))}
                placeholder={activeRoom?.room_key ? activeRoom.room_key : 'Enter key to join, or leave blank to create'}
                aria-label="Scouting room key"
                disabled={!hasScoutProfile}
              />
              <button
                type="button"
                className="center-btn"
                onClick={() => { void createOrJoinRoom(); }}
                disabled={!hasScoutProfile}
              >
                {activeRoom?.room_key
                  ? roomKeyInput && normalizeRoomKey(roomKeyInput) !== normalizeRoomKey(activeRoom.room_key)
                    ? 'Switch Room'
                    : 'Rejoin'
                  : roomKeyInput
                    ? 'Join Room'
                    : 'Create Room'}
              </button>
            </div>
            <div className="center-actions-row compact">
              <button
                type="button"
                className="center-btn ghost"
                onClick={() => {
                  if (activeRoom?.room_key) {
                    setRoomQrOpen(true);
                    return;
                  }
                  setQrImportOpen(true);
                }}
                disabled={!hasScoutProfile}
              >
                <QrCodeIcon className="icon-inline" /> {activeRoom?.room_key ? 'QR Code' : 'Scan QR Code'}
              </button>
            </div>
            {activeRoom?.room_key ? (
              <>
                <div className="center-status-row compact scout-status-row">
                  <span className="center-chip">Room: {activeRoom.room_key}</span>
                  <span className={`center-chip timer ${roomConnectionState === 'connected' ? 'live' : roomConnectionState === 'connecting' ? 'upcoming' : 'ended'}`.trim()}>
                    {roomConnectionState === 'connected' ? <><WifiIcon className="icon-inline" /> Connected</> : roomConnectionState === 'connecting' ? 'Connecting…' : <><WifiOffIcon className="icon-inline" /> Disconnected</>}
                  </span>
                  <span className="center-chip">Members: {roomPresence.reduce((sum, member) => sum + member.connections, 0)}</span>
                  <span className="center-chip">
                    Leader: {roomLeaderProfile || 'N/A'}
                  </span>
                </div>
                {roomPresence.length > 0 ? (
                  <div className="scout-profile-chip-row">
                    {roomPresence.map((member) => (
                      <span key={`presence-${member.scout_profile}`} className="scout-profile-chip active">
                        {member.scout_profile} ({member.connections})
                      </span>
                    ))}
                  </div>
                ) : null}
                {secondaryLeaderProfiles.length > 0 ? (
                  <p className="center-callout muted" style={{ marginTop: '0.55rem' }}>
                    Secondary leaders: <strong>{secondaryLeaderProfiles.join(', ')}</strong>
                  </p>
                ) : null}
                {hasRoomOwnerAuthority ? (
                  <div className="scout-profile-grid" style={{ marginTop: '0.55rem' }}>
                    <p className="center-callout muted" style={{ marginBottom: '0.25rem' }}>
                      Leader controls
                    </p>
                    {promotableRoomMembers.length > 0 ? (
                      <div className="center-actions-row compact">
                        {promotableRoomMembers.map((member) => (
                          <button
                            key={`promote-member-${member.scout_profile}`}
                            type="button"
                            className="center-btn ghost"
                            onClick={() => { void promoteSecondaryLeader(member.scout_profile); }}
                            disabled={roomPromotePendingProfile.toLowerCase() === member.scout_profile.toLowerCase()}
                          >
                            {roomPromotePendingProfile.toLowerCase() === member.scout_profile.toLowerCase()
                              ? `Promoting ${member.scout_profile}…`
                              : `Promote ${member.scout_profile}`}
                          </button>
                        ))}
                      </div>
                    ) : null}
                    {demotableSecondaryLeaders.length > 0 ? (
                      <div className="center-actions-row compact">
                        {demotableSecondaryLeaders.map((profile) => (
                          <button
                            key={`demote-leader-${profile}`}
                            type="button"
                            className="center-btn ghost"
                            onClick={() => { void demoteSecondaryLeader(profile); }}
                            disabled={roomDemotePendingProfile.toLowerCase() === profile.toLowerCase()}
                          >
                            {roomDemotePendingProfile.toLowerCase() === profile.toLowerCase()
                              ? `Removing ${profile}…`
                              : `Remove Leader ${profile}`}
                          </button>
                        ))}
                      </div>
                    ) : null}
                    {kickableRoomMembers.length > 0 ? (
                      <div className="center-actions-row compact">
                        {kickableRoomMembers.map((member) => (
                          <button
                            key={`kick-member-${member.scout_profile}`}
                            type="button"
                            className="center-btn ghost"
                            onClick={() => { void kickRoomMember(member.scout_profile); }}
                            disabled={roomKickPendingProfile.toLowerCase() === member.scout_profile.toLowerCase()}
                          >
                            {roomKickPendingProfile.toLowerCase() === member.scout_profile.toLowerCase()
                              ? `Removing ${member.scout_profile}…`
                              : `Remove ${member.scout_profile}`}
                          </button>
                        ))}
                      </div>
                    ) : null}
                  </div>
                ) : null}
                {myRoomAssignments.length > 0 ? (
                  <div className="scout-profile-grid" style={{ marginTop: '0.55rem' }}>
                    <p className="center-callout muted" style={{ marginBottom: '0.25rem' }}>
                      Assigned to you: {myRoomAssignments.length} slot{myRoomAssignments.length === 1 ? '' : 's'}.
                    </p>
                    <div className="center-actions-row compact">
                      {myRoomAssignments.slice(0, 12).map((assignment) => (
                        <button
                          key={`my-room-assignment-${assignment.assignment_id}-${assignment.match_key}-${assignment.team_key}`}
                          type="button"
                          className="center-btn ghost"
                          onClick={() => {
                            setSelectedMatchKey(assignment.match_key);
                            setSelectedTeamKey(assignment.team_key);
                            setStatusText(`Loaded assignment ${assignment.match_display} · ${assignment.team_display}.`);
                          }}
                          title={`Load ${assignment.match_display} ${assignment.team_display}`}
                        >
                          {assignment.match_display} · {assignment.team_display}
                        </button>
                      ))}
                    </div>
                  </div>
                ) : (
                  <p className="center-callout muted" style={{ marginTop: '0.55rem' }}>
                    No room assignments currently mapped to scout profile <strong>{scoutProfile || 'N/A'}</strong>.
                  </p>
                )}
                <div className="center-actions-row">
                  <button type="button" className="center-btn ghost" onClick={copyRoomKey}>
                    <CopyIcon className="icon-inline" /> Copy Key
                  </button>
                  <button type="button" className="center-btn ghost" onClick={reconnectRoomSocket}>
                    <RefreshIcon className="icon-inline" /> Reconnect
                  </button>
                  <button type="button" className="center-btn ghost" onClick={leaveRoom}>
                    <LogOutIcon className="icon-inline" /> Leave Room
                  </button>
                </div>
              </>
            ) : (
              <p className="center-callout muted">
                Not in a room. Entries saved locally.
              </p>
            )}
            {roomErrorText ? <p className="center-callout warning">{roomErrorText}</p> : null}
          </div>
        </SurfaceCard>
        </div>
        ) : null}

        {showDataSidebarSection ? (
        <div
          id="scouting-sidebar-data"
          ref={dataSectionRef}
          className={`scout-sidebar-section ${sidebarSection === 'data' ? 'active' : ''}`.trim()}
          onFocusCapture={() => setSidebarSection('data')}
        >
          <div className="scout-sidebar-section-head">
            <span className="scout-sidebar-section-label">Data</span>
            <span className="scout-sidebar-section-note">{dataSectionSummary}</span>
          </div>
        <SurfaceCard title="Auto Draft" subtitle="Generate, review, and save with evidence.">
          <AutoScoutReviewPanel
            enabled={autoScoutEnabled}
            canGenerate={Boolean(selectedEventKey && selectedMatchKey && selectedTeamKey)}
            loading={autoScout.loading}
            approving={autoScout.approving}
            error={autoScout.error}
            draft={autoScout.draft}
            onEnableAuto={() => setEntryCaptureMode('auto')}
            onDisableAuto={() => setEntryCaptureMode('manual')}
            onGenerate={() => { void autoScout.generateDraft(false); }}
            onRegenerate={() => { void autoScout.generateDraft(true); }}
            onReject={() => { void autoScout.rejectDraft('Scout rejected this draft during review.'); }}
            onSave={() => { void saveScoutingEntry(); }}
          />
        </SurfaceCard>

        {autoScoutEnabled && autoScoutInsightRows.length > 0 ? (
        <SurfaceCard title="Auto Insights" subtitle="Machine-derived signals kept outside the scouting form.">
          <div className="scout-profile-grid">
            {autoScoutInsightRows.map(([insightKey, insightValue]) => (
              <button
                key={`auto-insight-${insightKey}`}
                type="button"
                className="center-btn ghost"
                onClick={() => { void autoScout.openEvidence(insightKey); }}
              >
                <span>{titleizeKey(insightKey)}</span>
                <strong>
                  {typeof insightValue === 'object' ? JSON.stringify(insightValue) : String(insightValue)}
                </strong>
              </button>
            ))}
          </div>
        </SurfaceCard>
        ) : null}

        <SurfaceCard title="Match Timer" subtitle="Phase awareness for live scouting.">
          <div className="scout-timer-shell">
            <div className="scout-timer-value">{timerClockLabel}</div>
            <span className={`center-chip timer ${timerPhaseState}`.trim()}>
              Phase: {timerPhase}
            </span>
            <span className={`center-chip timer ${liveTimer.state}`.trim()}>
              {liveTimer.label}: {liveTimer.value}
            </span>
          </div>
          <div className="center-actions-row">
            <button type="button" className="center-btn" onClick={() => setTimerRunning((current) => !current)}>
              {timerRunning ? <><PauseIcon className="icon-inline" /> Pause</> : <><PlayIcon className="icon-inline" /> Start</>}
            </button>
            <button
              type="button"
              className="center-btn ghost"
              onClick={() => {
                setTimerSec(MATCH_DURATION_SEC);
                setTimerRunning(false);
              }}
            >
              <ResetIcon className="icon-inline" /> Reset
            </button>
            <button type="button" className="center-btn ghost" onClick={syncTimerToMatch}>
              <RefreshIcon className="icon-inline" /> Sync to Match Clock
            </button>
          </div>
          <p className="center-callout muted">
            Start/Pause while scouting live. Sync pulls from match schedule.
          </p>
        </SurfaceCard>

        <SurfaceCard title="Save + Data" subtitle="Ratings generated on save.">
          <div className="center-actions-row">
            <button
              type="button"
              className="center-btn"
              onClick={() => {
                void saveScoutingEntry();
              }}
              disabled={!selectedEventKey || !selectedMatchKey || !selectedTeamKey || savingEntry || !hasScoutProfile}
            >
              {savingEntry ? 'Saving...' : <><SaveIcon className="icon-inline" /> Save Scouting Entry</>}
            </button>
          </div>
          <div className="center-actions-row compact">
            <button type="button" className="center-btn ghost" onClick={clearAllEntries} disabled={entries.length === 0}>
              <TrashIcon className="icon-inline" /> Clear All
            </button>
            <button type="button" className="center-btn ghost" onClick={exportEntriesCsv} disabled={entries.length === 0}>
              <DownloadIcon className="icon-inline" /> Export CSV
            </button>
            <button type="button" className="center-btn ghost" onClick={exportEntriesHtml} disabled={entries.length === 0}>
              <DownloadIcon className="icon-inline" /> Export HTML
            </button>

            <button type="button" className="center-btn ghost" onClick={() => setQrImportOpen(true)}>
              <QrCodeIcon className="icon-inline" /> Import QR
            </button>
          </div>
          <div className="center-status-row compact scout-status-row">
            <span className="center-chip">Saved entries: {entries.length}</span>
            <span className="center-chip">Last save: {relativeFromTimestamp(lastSavedAt)}</span>
            <span className="center-chip">Scout: {scoutProfile || 'Not set'}</span>
          </div>
          {errorText ? <p className="center-callout danger">{errorText}</p> : null}
          <p className="center-callout muted">{statusText}</p>
        </SurfaceCard>
        </div>
        ) : null}
        </div>
      </aside>

      <section className="center-main">
        {isMobileLayout ? (
          <>
            <div className="fm-scout-hero" role="region" aria-label="Mobile scouting quick context">
              <div className="fm-scout-hero-actions">
                <button
                  type="button"
                  className="center-btn"
                  onClick={() => {
                    void saveScoutingEntry();
                  }}
                  disabled={!selectedEventKey || !selectedMatchKey || !selectedTeamKey || savingEntry || !hasScoutProfile}
                >
                  {savingEntry ? 'Saving...' : <><SaveIcon className="icon-inline" /> Save</>}
                </button>
              </div>
              <div className="fm-scout-hero-context" role="list" aria-label="Core scouting context">
                <span className="fm-scout-hero-chip match" role="listitem"><strong>{selectedMatch?.display_name || 'N/A'}</strong></span>
                <span className="fm-scout-hero-chip team" role="listitem"><strong>{selectedTeamKey ? selectedTeamKey.toUpperCase() : 'N/A'}</strong></span>
                <span className="fm-scout-hero-chip mode" role="listitem">{autoScoutEnabled ? 'Auto Draft' : 'Manual'}</span>
                <span className="fm-scout-hero-chip points" role="listitem">Pts <strong>{pointsSummary.total}</strong></span>
                <span className={`fm-scout-hero-chip timer ${timerPhaseState}`.trim()} role="listitem">
                  {timerClockLabel} · {timerPhase}
                </span>
              </div>
            </div>
            {!mobileFinderOpen ? (
              <div className="fm-scout-cards">
                <SegmentedTabs
                  className="fm-scout-section-tabs"
                  itemClassName="fm-scout-section-tab"
                  ariaLabel="Scouting section view"
                  value={mobileScoutSection}
                  onChange={setMobileScoutSection}
                  items={[
                    { value: 'capture', label: 'Capture', icon: <CameraIcon className="icon-inline" /> },
                    { value: 'score', label: 'Summary', icon: <StarIcon className="icon-inline" /> },
                  ]}
                />
                {mobileScoutSection === 'capture' ? (
                  <SegmentedTabs
                    className="fm-scout-panel-tabs"
                    itemClassName="fm-scout-panel-pill"
                    ariaLabel="Capture panels"
                    value={mobileCapturePanel}
                    onChange={(panel) => {
                      if (panel === 'auto-paths') {
                        navigate(`/scouting/auto-paths${searchParams.toString() ? `?${searchParams.toString()}` : ''}`);
                      } else {
                        setMobileCapturePanel(panel);
                      }
                    }}
                    items={MOBILE_CAPTURE_PANEL_TABS.map((panel) => ({
                      value: panel.id,
                      label: panel.label,
                      icon:
                        panel.id === 'auto' ? <RobotIcon className="icon-inline" />
                          : panel.id === 'teleop' ? <GamepadIcon className="icon-inline" />
                            : panel.id === 'endgame' ? <FlagIcon className="icon-inline" />
                              : panel.id === 'mobility' ? <MapPinIcon className="icon-inline" />
                                : panel.id === 'strategy' ? <TargetIcon className="icon-inline" />
                                  : panel.id === 'auto-paths' ? <MapPinIcon className="icon-inline" />
                                    : <PenIcon className="icon-inline" />,
                    }))}
                  />
                ) : null}
                {mobileScoutSection === 'score' ? (
                  <SegmentedTabs
                    className="fm-scout-panel-tabs"
                    itemClassName="fm-scout-panel-pill"
                    ariaLabel="Score panels"
                    value={mobileScorePanel}
                    onChange={setMobileScorePanel}
                    items={MOBILE_SCORE_PANEL_TABS.filter((panel) => panel.id === 'points' || panel.id === 'live').map((panel) => ({
                      value: panel.id,
                      label: panel.label,
                      icon:
                        panel.id === 'driver' ? <SteeringWheelIcon className="icon-inline" />
                          : panel.id === 'points' ? <StarIcon className="icon-inline" />
                            : panel.id === 'live' ? <LiveDotIcon className="icon-inline" />
                              : <SaveIcon className="icon-inline" />,
                    }))}
                  />
                ) : null}
              </div>
            ) : null}
          </>
        ) : null}

        {!isMobileLayout && autoScout.evidenceField ? (
          <AutoScoutEvidencePanel
            open={Boolean(autoScout.evidenceField)}
            mobile={false}
            fieldName={autoScout.evidenceField}
            draft={autoScout.draft}
            tracksData={autoScout.tracksData}
            tracksLoading={autoScout.tracksLoading}
            tracksError={autoScout.tracksError}
            onClose={autoScout.closeEvidence}
          />
        ) : null}

        {showCaptureSection ? (
          <div className={`scout-main-grid scout-main-grid-extended ${!isMobileLayout ? 'scout-main-grid-capture-desktop' : ''} ${isMobileLayout ? 'fm-scout-cards' : ''}`.trim()}>
          {showDesktopStackedAutoEndgame ? (
            <div className="scout-capture-stack-col">
              {captureAutoCard}
              <div className="scout-capture-stack-divider" aria-hidden="true" />
              {captureEndgameCard}
            </div>
          ) : (
            <>
              {captureAutoCard}
              {captureEndgameCard}
            </>
          )}

          {showCaptureTeleopCard ? (
          <SurfaceCard title="Teleop" subtitle="Production, misses, and handling.">
            <div className="scout-control-card">
              {countersFor('teleop').map((field) => (
                <CounterInput
                  key={field.key}
                  label={field.label}
                  value={form[field.key]}
                  onChange={(next) => updateCounter(field.key, next)}
                  step={field.step}
                  badge={renderAutoScoutBadge(field.key)}
                />
              ))}
            </div>
          </SurfaceCard>
          ) : null}

          {showCaptureMobilityCard ? (
          <SurfaceCard title="Anti-Defense + Mobility" subtitle="Pressure survival.">
            <div className="scout-control-card">
              {countersFor('mobility').map((field) => (
                <CounterInput
                  key={field.key}
                  label={field.label}
                  value={form[field.key]}
                  onChange={(next) => updateCounter(field.key, next)}
                  step={field.step}
                  badge={renderAutoScoutBadge(field.key)}
                />
              ))}
              {scalesFor('mobility').map((field) => (
                <ScaleInput
                  key={field.key}
                  label={field.label}
                  value={form[field.key]}
                  onChange={(next) => setForm((current) => ({ ...current, [field.key]: next }))}
                  badge={renderAutoScoutBadge(field.key)}
                />
              ))}
            </div>
          </SurfaceCard>
          ) : null}
          </div>
        ) : null}

        {showCaptureSection ? (
          <div className={`scout-main-grid scout-secondary-grid ${isMobileLayout ? 'fm-scout-cards' : ''}`.trim()}>
          {showCaptureStrategyCards ? (
          <SurfaceCard title="Driver + Strategy Scales" subtitle="1-5 qualitative signals.">
            <div className="scout-strategy-grid">
              {scalesFor('strategy').map((field) => (
                <ScaleInput
                  key={field.key}
                  label={field.label}
                  value={form[field.key]}
                  onChange={(next) => setForm((current) => ({ ...current, [field.key]: next }))}
                  badge={renderAutoScoutBadge(field.key)}
                />
              ))}
            </div>
          </SurfaceCard>
          ) : null}

          {showCaptureStrategyCards ? (
          <SurfaceCard title="RP Checks" subtitle="Suggestions + manual override.">
            <div className="center-actions-row">
              <button type="button" className="center-btn ghost" onClick={applySuggestedRp}>
                <TargetIcon className="icon-inline" /> Apply Suggested RP
              </button>
            </div>
            <p className="center-callout muted">
              RP predictions from scout inputs. Toggle manually before save.
            </p>
            <div className="scout-rp-grid">
              {(
                [
                  ['energized', 'Energized RP', rpSuggested.energized],
                  ['supercharged', 'Supercharged RP', rpSuggested.supercharged],
                  ['traversal', 'Traversal RP', rpSuggested.traversal],
                  ['coop', 'Co-op RP', false],
                ] as Array<[keyof RpState, string, boolean]>
              ).map(([key, label, suggested]) => (
                <button
                  key={`rp-${key}`}
                  type="button"
                  className={`scout-rp-btn ${rpState[key] ? 'active' : ''}`.trim()}
                  onClick={() => setRpState((current) => ({ ...current, [key]: !current[key] }))}
                >
                  <strong>{label}</strong>
                  <small>{suggested ? 'Suggested by points model' : 'Manual toggle'}</small>
                </button>
              ))}
            </div>
          </SurfaceCard>
          ) : null}
          </div>
        ) : null}

        {showScoreSection ? (
          <div className={`scout-main-grid scout-secondary-grid scout-tertiary-grid ${isMobileLayout ? 'fm-scout-cards' : ''}`.trim()}>
          {showScoreDriverCard ? (
          <SurfaceCard title="Driver Competency" subtitle="Teleop + endgame + offense/defense (auto excluded).">
            <div className="scout-driver-grid">
              <div className="scout-driver-score">
                <span>Driver score</span>
                <strong>{driverScore.score_0_100}</strong>
                <small>
                  Level {driverScore.level_1_5}/5 · {driverScore.tier}
                </small>
              </div>
              <div className="scout-driver-breakdown">
                <div>
                  <span>Teleop volume</span>
                  <strong>{Math.round(driverScore.breakdown.teleop_volume_0_1 * 100)}%</strong>
                </div>
                <div>
                  <span>Teleop accuracy</span>
                  <strong>{Math.round(driverScore.breakdown.teleop_accuracy_0_1 * 100)}%</strong>
                </div>
                <div>
                  <span>Offense signal</span>
                  <strong>{Math.round(driverScore.breakdown.offense_0_1 * 100)}%</strong>
                </div>
                <div>
                  <span>Defense signal</span>
                  <strong>{Math.round(driverScore.breakdown.defense_0_1 * 100)}%</strong>
                </div>
                <div>
                  <span>Endgame signal</span>
                  <strong>{Math.round(driverScore.breakdown.endgame_0_1 * 100)}%</strong>
                </div>
                <div>
                  <span>Discipline signal</span>
                  <strong>{Math.round(driverScore.breakdown.discipline_0_1 * 100)}%</strong>
                </div>
              </div>
            </div>
            <p className="center-callout muted">
              Auto excluded — reflects driver-controlled phases only.
            </p>
          </SurfaceCard>
          ) : null}

          {showScorePointsCard ? (
          <SurfaceCard title="Point Summary" subtitle="Match totals from tap counts.">
            <div className="center-kpi-grid">
              <div className="center-kpi-card">
                <span>Auto points</span>
                <strong>{pointsSummary.auto}</strong>
              </div>
              <div className="center-kpi-card">
                <span>Teleop points</span>
                <strong>{pointsSummary.teleop}</strong>
              </div>
              <div className="center-kpi-card">
                <span>Endgame points</span>
                <strong>{pointsSummary.endgame}</strong>
              </div>
              <div className="center-kpi-card">
                <span>Total points</span>
                <strong>{pointsSummary.total}</strong>
              </div>
            </div>
          </SurfaceCard>
          ) : null}

          {showScoreLiveCard ? (
          <SurfaceCard title="Live Robot Rating" subtitle="Real-time rating from scout inputs.">
            <div className="center-kpi-grid">
              <div className="center-kpi-card">
                <span>Overall scout score (0-100)</span>
                <strong>{liveOverallScoutRating.score_0_100.toFixed(1)}</strong>
              </div>
              <div className="center-kpi-card">
                <span>Robot ranking</span>
                <strong>
                  {liveOverallScoutRating.tier} · L{liveRobotRankLevel}/5
                </strong>
              </div>
            </div>
            <p className="center-callout muted">
              Updates instantly from scout data. Saved ratings appear after save.
            </p>
          </SurfaceCard>
          ) : null}

          {showScoreSavedCard ? (
          <SurfaceCard title="Saved Report Ratings" subtitle="Ratings appear after save.">
            {!latestSavedEntry ? (
              <p className="center-callout muted">Save a report to generate ratings.</p>
            ) : (
              <div className="scout-score-shell">
                <div className="scout-score-grid">
                  <div className="scout-score-card">
                    <span>Overall scout score</span>
                    <strong>{latestSavedEntry.overall_scout_rating.score_0_100.toFixed(1)}</strong>
                    <small>{latestSavedEntry.overall_scout_rating.tier}</small>
                  </div>
                  <div className="scout-score-card">
                    <span>Manual scouting score</span>
                    <strong>{latestSavedEntry.manual_rating.score_0_100.toFixed(1)}</strong>
                    <small>{latestSavedEntry.manual_rating.tier}</small>
                  </div>
                  <div className="scout-score-card">
                    <span>Scout+API score</span>
                    <strong>{latestSavedEntry.scouting_api_rating?.score_0_100.toFixed(1) || 'N/A'}</strong>
                    <small>
                      {latestSavedEntry.scouting_api_rating
                        ? `${latestSavedEntry.scouting_api_rating.tier} · conf ${(latestSavedEntry.scouting_api_rating.confidence_0_1 * 100).toFixed(0)}%`
                        : 'API context unavailable'}
                    </small>
                  </div>
                </div>
                <p className="center-callout muted">Head-up: {entryHeadUp(latestSavedEntry)}</p>
                <div className="scout-pros-cons-grid">
                  <article className="scout-list-block">
                    <h5>Pros</h5>
                    {latestSavedEntry.scouting_api_rating?.pros?.slice(0, 3).map((value, idx) => (
                      <p key={`saved-pro-${idx}`}>{value}</p>
                    ))}
                  </article>
                  <article className="scout-list-block">
                    <h5>Cons</h5>
                    {latestSavedEntry.scouting_api_rating?.cons?.slice(0, 3).map((value, idx) => (
                      <p key={`saved-con-${idx}`}>{value}</p>
                    ))}
                  </article>
                </div>
                {latestSavedEntry.notes ? <p className="center-callout muted">Notes: {latestSavedEntry.notes}</p> : null}
              </div>
            )}
          </SurfaceCard>
          ) : null}
          </div>
        ) : null}

        {showHistorySection && showHistoryTeamMatchesCard ? (
          <SurfaceCard
            title="Selected Team Match Performance"
            subtitle="Per-match performance for selected team."
          >
          {!selectedTeamKey ? (
            <p className="center-callout muted">Pick a team to load match rows.</p>
          ) : selectedTeamMatchPerformance.length === 0 ? (
            <p className="center-callout muted">
              No schedule rows for {selectedTeamKey.toUpperCase()} in {selectedEventKey || 'this event'}.
            </p>
          ) : isMobileLayout ? (
            <div className="center-mobile-card-list">
              {selectedTeamMatchPerformance.map((row) => {
                const statusClass =
                  row.status === 'live'
                    ? 'live'
                    : row.status === 'upcoming'
                      ? 'upcoming'
                      : row.status === 'completed'
                        ? 'ended'
                        : 'unknown';
                const latestNote = row.latest_note
                  ? row.latest_note.length > 120
                    ? `${row.latest_note.slice(0, 120)}...`
                    : row.latest_note
                  : null;
                return (
                  <article key={`team-match-performance-mobile-${row.match_key}`} className="center-mobile-data-card">
                    <header>
                      <div>
                        <strong>{row.match_display}</strong>
                        <div className="meta">
                          {fmtDateShort(row.scheduled_time)} · {row.comp_level}
                        </div>
                      </div>
                      <span className={`center-chip timer ${statusClass}`.trim()}>{titleizeKey(row.status)}</span>
                    </header>
                    <div className="center-mobile-data-grid">
                      <span>
                        Score
                        <strong>
                          {row.alliance_score !== null && row.opponent_score !== null
                            ? `${row.alliance_score}-${row.opponent_score}`
                            : 'N/A'}
                        </strong>
                      </span>
                      <span>
                        Reports<strong>{row.scout_reports}</strong>
                      </span>
                      <span>
                        Overall<strong>{row.overall_scout_avg_0_100 !== null ? row.overall_scout_avg_0_100.toFixed(1) : 'N/A'}</strong>
                      </span>
                      <span>
                        Manual<strong>{row.manual_avg_0_100 !== null ? row.manual_avg_0_100.toFixed(1) : 'N/A'}</strong>
                      </span>
                      <span>
                        Scout+API
                        <strong>{row.scouting_api_avg_0_100 !== null ? row.scouting_api_avg_0_100.toFixed(1) : 'N/A'}</strong>
                      </span>
                      <span>
                        Avg Pts<strong>{row.avg_points_total !== null ? row.avg_points_total.toFixed(1) : 'N/A'}</strong>
                      </span>
                    </div>
                    {latestNote ? <p className="center-callout muted scout-mobile-note">Note: {latestNote}</p> : null}
                    <div className="center-actions-row compact scout-mobile-actions">
                      <button
                        type="button"
                        className="center-btn ghost"
                        onClick={() => {
                          setSelectedMatchKey(row.match_key);
                          setSelectedTeamKey(selectedTeamKey);
                          setStatusText(`Loaded ${row.match_display} for scouting.`);
                        }}
                      >
                        <ClipboardIcon className="icon-inline" /> Scout Match
                      </button>
                      <Link className="center-btn ghost" to={`/match-center?event=${selectedEventKey}&match=${row.match_key}`}>
                        <ScoreboardIcon className="icon-inline" /> Match Center
                      </Link>
                    </div>
                  </article>
                );
              })}
            </div>
          ) : (
            <div className="center-table-wrap">
              <table className="center-table">
                <thead>
                  <tr>
                    <th>Match</th>
                    <th>Type</th>
                    <th>Status</th>
                    <th>Score</th>
                    <th>Reports</th>
                    <th>Overall</th>
                    <th>Manual</th>
                    <th>Scout+API</th>
                    <th>Avg Pts</th>
                    <th>Latest Note</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {selectedTeamMatchPerformance.map((row) => (
                    <tr key={`team-match-performance-${row.match_key}`}>
                      <td>
                        <strong>{row.match_display}</strong>
                        <small>{fmtDateShort(row.scheduled_time)}</small>
                      </td>
                      <td>{row.comp_level}</td>
                      <td>
                        <span className={`center-chip timer ${row.status === 'live' ? 'live' : row.status === 'upcoming' ? 'upcoming' : row.status === 'completed' ? 'ended' : 'unknown'}`.trim()}>
                          {titleizeKey(row.status)}
                        </span>
                      </td>
                      <td>
                        {row.alliance_score !== null && row.opponent_score !== null
                          ? `${row.alliance_score} - ${row.opponent_score}`
                          : 'N/A'}
                      </td>
                      <td>{row.scout_reports}</td>
                      <td>{row.overall_scout_avg_0_100 !== null ? row.overall_scout_avg_0_100.toFixed(1) : 'N/A'}</td>
                      <td>{row.manual_avg_0_100 !== null ? row.manual_avg_0_100.toFixed(1) : 'N/A'}</td>
                      <td>{row.scouting_api_avg_0_100 !== null ? row.scouting_api_avg_0_100.toFixed(1) : 'N/A'}</td>
                      <td>{row.avg_points_total !== null ? row.avg_points_total.toFixed(1) : 'N/A'}</td>
                      <td>
                        {row.latest_note
                          ? row.latest_note.length > 100
                            ? `${row.latest_note.slice(0, 100)}...`
                            : row.latest_note
                          : 'No notes yet'}
                      </td>
                      <td>
                        <div className="center-actions-row compact">
                          <button
                            type="button"
                            className="center-btn ghost"
                            onClick={() => {
                              setSelectedMatchKey(row.match_key);
                              setSelectedTeamKey(selectedTeamKey);
                              setStatusText(`Loaded ${row.match_display} for scouting.`);
                            }}
                          >
                            <ClipboardIcon className="icon-inline" /> Scout Match
                          </button>
                          <Link className="center-btn ghost" to={`/match-center?event=${selectedEventKey}&match=${row.match_key}`}>
                            <ScoreboardIcon className="icon-inline" /> Match Center
                          </Link>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          </SurfaceCard>
        ) : null}

        {showCaptureSection && showCaptureNotesCard ? (
          <SurfaceCard
            title="Per-Game Scouting Notes"
            subtitle="Context that counters and scales can't capture."
          >
          <div className="scout-notes-shell">
            <textarea
              className="scout-notes-input"
              value={scoutNotes}
              onChange={(event) => setScoutNotes(normalizeScoutingNotes(event.target.value))}
              placeholder="Example: struggled against hard pin for first 45s, recovered after driver station call, missed final climb timing."
              aria-label="Scouting notes"
            />
            <div className="center-status-row compact">
              <span className="center-chip">{scoutNotes.length}/600</span>
              <span className="center-chip">Saved on report save</span>
            </div>
          </div>
          </SurfaceCard>
        ) : null}

        {showHistorySection && showHistoryTeamRollupsCard ? (
          <SurfaceCard
            title="Scouted Teams Quick Access"
            subtitle="Scouted team scores and head-up."
            right={
              !isMobileLayout ? (
                <div className="scout-history-toolbar">
                  <button
                    type="button"
                    className="center-btn ghost"
                    onClick={() => setShowTeamSummaries((current) => !current)}
                  >
                    {showTeamSummaries ? 'Hide Summary' : <><BarChartIcon className="icon-inline" /> Summary</>}
                  </button>
                  <label className="scout-history-filter">
                    <input
                      type="checkbox"
                      checked={historyMineOnly}
                      onChange={(event) => setHistoryMineOnly(event.target.checked)}
                      disabled={!activeRoom?.room_key}
                    />
                    {activeRoom?.room_key ? 'My reports only' : 'My reports'}
                  </label>
                  <label className="scout-history-filter">
                    <input
                      type="checkbox"
                      checked={historyOnlyCurrentEvent}
                      onChange={(event) => setHistoryOnlyCurrentEvent(event.target.checked)}
                    />
                    Current event only
                  </label>
                </div>
              ) : undefined
            }
          >
            <div className="center-input-row scout-quick-search-row">
              <input
                value={quickTeamQuery}
                onChange={(event) => setQuickTeamQuery(event.target.value)}
                placeholder="Filter scouted teams (team key or label)"
                aria-label="Filter scouted teams"
              />
            </div>
          {teamRollups.length === 0 ? (
            <p className="center-callout muted">No scouted teams in this scope.</p>
          ) : isMobileLayout ? (
            <div className="center-mobile-card-list">
              {teamRollups.slice(0, 60).map((row) => (
                <article key={`rollup-mobile-${row.team_key}`} className="center-mobile-data-card">
                  <header>
                    <div>
                      <strong>{row.team_key.toUpperCase()}</strong>
                      <div className="meta">{row.team_label}</div>
                    </div>
                    <span className="center-chip">Reports: {row.entries_count}</span>
                  </header>
                  <div className="center-mobile-data-grid">
                    <span>
                      Notes<strong>{row.notes_count}</strong>
                    </span>
                    <span>
                      Overall<strong>{row.avg_overall_scout_0_100.toFixed(1)}</strong>
                    </span>
                    <span>
                      Manual<strong>{row.avg_manual_0_100.toFixed(1)}</strong>
                    </span>
                    <span>
                      Scout+API
                      <strong>{row.avg_scouting_api_0_100 !== null ? row.avg_scouting_api_0_100.toFixed(1) : 'N/A'}</strong>
                    </span>
                    <span>
                      Avg Pts<strong>{row.avg_points_total.toFixed(1)}</strong>
                    </span>
                    <span>
                      Driver<strong>{row.avg_driver_0_100.toFixed(1)}</strong>
                    </span>
                  </div>
                  <p className="center-callout muted scout-mobile-note">{row.head_up}</p>
                  <div className="center-actions-row compact scout-mobile-actions">
                    <Link className="center-btn ghost" to={`/team-center?event=${row.latest_event_key}&team=${row.team_key}`}>
                      Team
                    </Link>
                    <Link className="center-btn ghost" to={`/match-center?event=${row.latest_event_key}&match=${row.latest_match_key}`}>
                      Match
                    </Link>
                    <button
                      type="button"
                      className="center-btn ghost"
                      onClick={() => {
                        setSelectedTeamKey(row.team_key);
                        setSelectedEventKey(row.latest_event_key);
                        setEventInput(row.latest_event_key);
                        setStatusText(`Focused on ${row.team_key.toUpperCase()}.`);
                      }}
                    >
                      <SearchIcon className="icon-inline" /> Focus
                    </button>
                  </div>
                </article>
              ))}
            </div>
          ) : (
            <div className="center-table-wrap">
              <table className="center-table">
                <thead>
                  <tr>
                    <th>Team</th>
                    <th>Reports</th>
                    <th>Notes</th>
                    <th>Overall</th>
                    <th>Manual</th>
                    <th>Scout+API</th>
                    <th>Avg Pts</th>
                    <th>Driver</th>
                    <th>Head-up</th>
                    <th>Quick Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {teamRollups.slice(0, 60).map((row) => (
                    <tr key={`rollup-${row.team_key}`}>
                      <td>
                        <strong>{row.team_key.toUpperCase()}</strong>
                        <small>{row.team_label}</small>
                      </td>
                      <td>{row.entries_count}</td>
                      <td>{row.notes_count}</td>
                      <td>{row.avg_overall_scout_0_100.toFixed(1)}</td>
                      <td>{row.avg_manual_0_100.toFixed(1)}</td>
                      <td>{row.avg_scouting_api_0_100 !== null ? row.avg_scouting_api_0_100.toFixed(1) : 'N/A'}</td>
                      <td>{row.avg_points_total.toFixed(1)}</td>
                      <td>{row.avg_driver_0_100.toFixed(1)}</td>
                      <td>{row.head_up}</td>
                      <td>
                        <div className="center-actions-row compact">
                          <Link className="center-btn ghost" to={`/team-center?event=${row.latest_event_key}&team=${row.team_key}`}>
                            Team
                          </Link>
                          <Link className="center-btn ghost" to={`/match-center?event=${row.latest_event_key}&match=${row.latest_match_key}`}>
                            Match
                          </Link>
                          <button
                            type="button"
                            className="center-btn ghost"
                            onClick={() => {
                              setSelectedTeamKey(row.team_key);
                              setSelectedEventKey(row.latest_event_key);
                              setEventInput(row.latest_event_key);
                              setStatusText(`Focused on ${row.team_key.toUpperCase()}.`);
                            }}
                          >
                            Focus
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          </SurfaceCard>
        ) : null}

        {showHistorySection && showHistorySummariesCard ? (
          <SurfaceCard
            title="Scouting Team Summaries"
            subtitle="Per-team summaries with notes."
          >
            {teamSummaries.length === 0 ? (
              <p className="center-callout muted">No summaries yet. Save reports first.</p>
            ) : (
              <div className="scout-team-summary-grid">
                {teamSummaries.map((row) => (
                  <article key={`team-summary-${row.team_key}`} className="scout-team-summary-card">
                    <header>
                      <h4>{row.team_key.toUpperCase()}</h4>
                      <small>{row.team_label}</small>
                    </header>
                    <p>{row.summary}</p>
                    <div className="center-status-row compact">
                      <span className="center-chip">Reports: {row.reports}</span>
                      <span className="center-chip">Overall: {row.avg_overall_scout_0_100.toFixed(1)}</span>
                      <span className="center-chip">Manual: {row.avg_manual_0_100.toFixed(1)}</span>
                      <span className="center-chip">
                        Scout+API: {row.avg_scouting_api_0_100 !== null ? row.avg_scouting_api_0_100.toFixed(1) : 'N/A'}
                      </span>
                      <span className="center-chip">Driver: {row.avg_driver_0_100.toFixed(1)}</span>
                    </div>
                    <div className="scout-team-summary-notes">
                      <strong>Scout Notes</strong>
                      {row.notes.length === 0 ? (
                        <p>No notes added yet for this team.</p>
                      ) : (
                        row.notes.map((note, idx) => <p key={`summary-note-${row.team_key}-${idx}`}>{note}</p>)
                      )}
                    </div>
                  </article>
                ))}
              </div>
            )}
          </SurfaceCard>
        ) : null}

        {showHistorySection && showHistoryEntriesCard ? (
          <SurfaceCard title="Scouted Matches" subtitle="Saved scouting logs and ratings.">
          {visibleEntries.length === 0 ? (
            <p className="center-callout muted">No entries yet. Save a scouting entry first.</p>
          ) : isMobileLayout ? (
            <div className="center-mobile-card-list">
              {visibleEntries.map((entry) => {
                const note = entry.notes
                  ? entry.notes.length > 120
                    ? `${entry.notes.slice(0, 120)}...`
                    : entry.notes
                  : null;
                return (
                  <article key={`entry-mobile-${entry.id}`} className="center-mobile-data-card">
                    <header>
                      <div>
                        <strong>{entry.team_key.toUpperCase()} · {entry.match_display}</strong>
                        <div className="meta">
                          {entry.event_key} · {relativeFromTimestamp(entry.saved_at_ms)} · {entry.scout_profile || 'Unknown scout'}
                        </div>
                      </div>
                      <span className="center-chip">Pts: {entry.points.total}</span>
                    </header>
                    <div className="center-mobile-data-grid">
                      <span>
                        Driver<strong>{entry.driver_competency.score_0_100}</strong>
                      </span>
                      <span>
                        Overall<strong>{entry.overall_scout_rating.score_0_100.toFixed(1)}</strong>
                      </span>
                      <span>
                        Manual<strong>{entry.manual_rating.score_0_100.toFixed(1)}</strong>
                      </span>
                      <span>
                        Scout+API
                        <strong>{entry.scouting_api_rating ? entry.scouting_api_rating.score_0_100.toFixed(1) : 'N/A'}</strong>
                      </span>
                      <span>
                        Room<strong>{activeRoom?.room_key ? activeRoom.room_key : 'N/A'}</strong>
                      </span>
                      <span>
                        Head-up<strong>{entryHeadUp(entry)}</strong>
                      </span>
                    </div>
                    <div className="center-actions-row compact scout-mobile-actions">
                      <Link className="center-btn ghost" to={`/match-center?event=${entry.event_key}&match=${entry.match_key}`}>
                        <ScoreboardIcon className="icon-inline" /> Match
                      </Link>
                      <Link className="center-btn ghost" to={`/team-center?event=${entry.event_key}&team=${entry.team_key}`}>
                        <UsersIcon className="icon-inline" /> Team
                      </Link>
                      <button type="button" className="center-btn ghost" onClick={() => setQrShareEntry(entry)}>
                        <QrCodeIcon className="icon-inline" /> Share QR
                      </button>
                    </div>
                    {note ? <p className="center-callout muted scout-mobile-note">Note: {note}</p> : null}
                  </article>
                );
              })}
            </div>
          ) : (
            <div className="center-table-wrap">
              <table className="center-table">
                <thead>
                  <tr>
                    <th>Saved</th>
                    <th>Scout</th>
                    <th>Event</th>
                    <th>Match</th>
                    <th>Team</th>
                    <th>Notes</th>
                    <th>Total</th>
                    <th>Driver</th>
                    <th>Overall</th>
                    <th>Manual</th>
                    <th>Scout+API</th>
                    <th>Head-up</th>
                    <th>Room</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {visibleEntries.map((entry) => (
                    <tr key={entry.id}>
                      <td>{relativeFromTimestamp(entry.saved_at_ms)}</td>
                      <td>{entry.scout_profile}</td>
                      <td>{entry.event_key}</td>
                      <td>
                        <Link to={`/match-center?event=${entry.event_key}&match=${entry.match_key}`}>
                          {entry.match_display}
                        </Link>
                      </td>
                      <td>
                        <Link to={`/team-center?event=${entry.event_key}&team=${entry.team_key}`}>
                          {entry.team_key.toUpperCase()}
                        </Link>
                      </td>
                      <td>
                        {entry.notes
                          ? entry.notes.length > 100
                            ? `${entry.notes.slice(0, 100)}...`
                            : entry.notes
                          : '—'}
                      </td>
                      <td>{entry.points.total}</td>
                      <td>{entry.driver_competency.score_0_100}</td>
                      <td>{entry.overall_scout_rating.score_0_100.toFixed(1)}</td>
                      <td>{entry.manual_rating.score_0_100.toFixed(1)}</td>
                      <td>{entry.scouting_api_rating ? entry.scouting_api_rating.score_0_100.toFixed(1) : 'N/A'}</td>
                      <td>{entryHeadUp(entry)}</td>
                      <td>
                        {activeRoom?.room_key ? activeRoom.room_key : 'Not connected'}
                      </td>
                      <td>
                        <button type="button" className="center-btn ghost" style={{ fontSize: '0.75rem', padding: '2px 6px' }} onClick={() => setQrShareEntry(entry)}>
                          QR
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          </SurfaceCard>
        ) : null}
      </section>
    </div>
    <AutoScoutEvidencePanel
      open={isMobileLayout && Boolean(autoScout.evidenceField)}
      mobile={isMobileLayout}
      fieldName={autoScout.evidenceField}
      draft={autoScout.draft}
      tracksData={autoScout.tracksData}
      tracksLoading={autoScout.tracksLoading}
      tracksError={autoScout.tracksError}
      onClose={autoScout.closeEvidence}
    />
    {qrShareEntry ? (
      <QrShareModal entry={qrShareEntry} onClose={() => setQrShareEntry(null)} />
    ) : null}
    {roomQrOpen && activeRoom?.room_key ? (
      <RoomQrModal roomKey={activeRoom.room_key} onClose={() => setRoomQrOpen(false)} />
    ) : null}
    {qrImportOpen ? (
      <QrImportModal
        onImport={(imported) => {
          setEntries((current) => mergeEntry(current, imported));
          setStatusText(`Imported entry: ${imported.team_label} · ${imported.match_display}`);
        }}
        onRoomJoin={async (roomKey) => {
          setRoomKeyInput(roomKey);
          const joined = await createOrJoinRoom({ overrideRoomKey: roomKey });
          if (!joined.ok) {
            throw new Error(joined.error || `Unable to join room ${roomKey}.`);
          }
          setQrImportOpen(false);
        }}
        onClose={() => setQrImportOpen(false)}
        existingIds={new Set(entries.map((e) => `${e.match_key}__${e.team_key}__${e.scout_profile}`))}
      />
    ) : null}
    </>
  );
}
