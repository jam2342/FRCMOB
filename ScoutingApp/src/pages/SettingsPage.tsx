import { useCallback, useEffect, useState } from 'react';
import { SurfaceCard } from '../components/ui/SurfaceCard';
import { PushNotificationsCard } from '../components/ui/PushNotificationsCard';
import {
  Button,
  CardBody,
  CardGrid,
  Chip,
  FieldSelect,
  FieldStepper,
  FieldText,
  Stat,
} from '../components/ui/primitives';
import styles from './SettingsPage.module.css';
import {
  clientAdminKeyAvailable,
  clearStoredClientAdminSessionToken,
  createAdminSession,
  getApiHealth,
  getOpsDashboard,
  type OpsDashboardResponse,
  getStoredClientAdminSessionExpiresAtUnix,
  getStoredClientAdminSessionToken,
  isClientAdminModeEnabled,
  runClimbOfficialBackfill,
  setClientAdminModeEnabled,
  setStoredClientAdminSessionToken,
} from '../api';
import {
  applyBodySettingsClasses,
  emitSettingsUpdated,
  FAVORITES_KEYS,
  getStoredSettings,
  readFavoriteEvents,
  readFavoriteTeams,
  saveStoredSettings,
  TUTORIAL_SCOPES,
  type DensityMode,
  type QuickJumpMode,
  type QuickJumpRegion,
  type ThemeMode,
  type UIMode,
} from '../layout/userSettings';
import {
  clearTutorialChecklist,
  clearTutorialSeen,
  countSeenTutorials,
  SCOUTING_TUTORIAL_PROGRESS_EVENT,
} from '../tutorial/tutorialState';
import { REGION_FILTER_OPTIONS } from '../utils/regionFilters';
import { relativeFromTimestamp } from './centerUtils';
import { AUTHOR_NAME, AUTHOR_SITE_URL } from './legalContent';

const COMPARE_KEYS = {
  event: 'scouting_compare_event_key',
  teams: 'scouting_compare_team_keys',
} as const;

type LocalDiagnostics = {
  favoriteEventsCount: number;
  favoriteTeamsCount: number;
  compareEventKey: string;
  compareTeamCount: number;
};

type AdminAuthStatusTone = 'muted' | 'success' | 'warning' | 'danger';

function readCompareTeamCount(): number {
  const raw = window.localStorage.getItem(COMPARE_KEYS.teams);
  if (!raw) return 0;
  try {
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return 0;
    return parsed
      .map((value) => String(value || '').trim().toLowerCase())
      .filter((value, idx, array) => /^frc\d+$/.test(value) && array.indexOf(value) === idx).length;
  } catch {
    return 0;
  }
}

function readLocalDiagnostics(): LocalDiagnostics {
  return {
    favoriteEventsCount: readFavoriteEvents().length,
    favoriteTeamsCount: readFavoriteTeams().length,
    compareEventKey: (window.localStorage.getItem(COMPARE_KEYS.event) || '').trim().toLowerCase(),
    compareTeamCount: readCompareTeamCount(),
  };
}

export function SettingsPage() {
  const initial = getStoredSettings();

  const [themeMode, setThemeMode] = useState<ThemeMode>(initial.theme);
  const [densityMode, setDensityMode] = useState<DensityMode>(initial.density);
  const [uiMode, setUiMode] = useState<UIMode>(initial.uiMode);
  const [quickJumpMode, setQuickJumpMode] = useState<QuickJumpMode>(initial.quickJumpMode);
  const [quickJumpRegion, setQuickJumpRegion] = useState<QuickJumpRegion>(initial.quickJumpRegion);
  const [liveRefreshSec, setLiveRefreshSec] = useState<number>(initial.liveRefreshSec);
  const [tutorialAutoplay, setTutorialAutoplay] = useState<boolean>(initial.tutorialAutoplay);

  const [statusText, setStatusText] = useState('Settings are synced locally.');
  const [lastSavedAt, setLastSavedAt] = useState<number>(() => Date.now());
  const [tutorialSeenCount, setTutorialSeenCount] = useState<number>(() => countSeenTutorials());
  const [healthStatus, setHealthStatus] = useState<string>('Checking API...');
  const [publicReadonlyMode, setPublicReadonlyMode] = useState<boolean | null>(null);
  const [writeAuthEnforced, setWriteAuthEnforced] = useState<boolean | null>(null);
  const [writeAuthHeader, setWriteAuthHeader] = useState<string>('X-Admin-Key');
  const [writeAuthSessionHeader, setWriteAuthSessionHeader] = useState<string>('X-Admin-Session');
  const [writeAuthKeyConfigured, setWriteAuthKeyConfigured] = useState<boolean | null>(null);
  const [diagnostics, setDiagnostics] = useState<LocalDiagnostics>(() => readLocalDiagnostics());
  const [opsDashboard, setOpsDashboard] = useState<OpsDashboardResponse | null>(null);
  const [opsLoading, setOpsLoading] = useState<boolean>(false);
  const [opsErrorText, setOpsErrorText] = useState<string>('');
  const [climbBackfillBusy, setClimbBackfillBusy] = useState<boolean>(false);
  const [climbBackfillStatus, setClimbBackfillStatus] = useState<string>('');
  const [adminApiKeyInput, setAdminApiKeyInput] = useState<string>('');
  const [adminSessionExpiresAtUnix, setAdminSessionExpiresAtUnix] = useState<number | null>(() =>
    getStoredClientAdminSessionExpiresAtUnix(),
  );
  const [adminModeEnabled, setAdminModeEnabled] = useState<boolean>(() => isClientAdminModeEnabled());
  const [adminAuthBusy, setAdminAuthBusy] = useState<boolean>(false);
  const [adminAuthStatus, setAdminAuthStatus] = useState<string>(() =>
    getStoredClientAdminSessionToken()
      ? 'Admin session is active for this browser tab.'
      : isClientAdminModeEnabled()
        ? 'Admin mode enabled, but no active session token.'
        : 'Admin mode disabled.',
  );
  const [adminAuthStatusTone, setAdminAuthStatusTone] = useState<AdminAuthStatusTone>(() =>
    getStoredClientAdminSessionToken() ? 'success' : isClientAdminModeEnabled() ? 'warning' : 'muted',
  );

  function refreshDiagnostics() {
    setDiagnostics(readLocalDiagnostics());
  }

  function refreshTutorialProgress() {
    setTutorialSeenCount(countSeenTutorials());
  }

  const refreshAdminSessionState = useCallback(() => {
    const token = getStoredClientAdminSessionToken();
    const expiresAtUnix = getStoredClientAdminSessionExpiresAtUnix();
    setAdminSessionExpiresAtUnix(expiresAtUnix);
    setAdminModeEnabled(isClientAdminModeEnabled());
    if (token) {
      setAdminAuthStatus('Admin session is active for this browser tab.');
      setAdminAuthStatusTone('success');
      return;
    }
    if (isClientAdminModeEnabled()) {
      setAdminAuthStatus('Admin mode enabled, but no active admin session token.');
      setAdminAuthStatusTone('warning');
      return;
    }
    setAdminAuthStatus('Admin mode disabled.');
    setAdminAuthStatusTone('muted');
  }, []);

  async function refreshOpsDashboard() {
    setOpsLoading(true);
    setOpsErrorText('');
    try {
      const payload = await getOpsDashboard(14);
      setOpsDashboard(payload);
    } catch (error) {
      setOpsDashboard(null);
      setOpsErrorText((error as Error).message || 'Unable to load ops dashboard.');
    } finally {
      setOpsLoading(false);
    }
  }

  async function runClimbBackfillNow() {
    setClimbBackfillBusy(true);
    setClimbBackfillStatus('Running climb official backfill...');
    try {
      const payload = await runClimbOfficialBackfill();
      const totals = payload.result?.totals;
      setClimbBackfillStatus(
        payload.ok
          ? `Backfill complete: ${totals?.updated_findings ?? 0} updated, ${totals?.inserted_findings ?? 0} inserted.`
          : 'Backfill finished with errors. Check backend logs.',
      );
      await refreshOpsDashboard();
    } catch (error) {
      setClimbBackfillStatus((error as Error).message || 'Backfill failed.');
    } finally {
      setClimbBackfillBusy(false);
    }
  }

  useEffect(() => {
    let cancelled = false;

    async function run() {
      try {
        const payload = await getApiHealth();
        if (cancelled) return;
        setHealthStatus(payload.ok ? 'API online' : 'API reported unavailable');
        setPublicReadonlyMode(typeof payload.public_readonly_mode === 'boolean' ? payload.public_readonly_mode : null);
        setWriteAuthEnforced(typeof payload.write_auth?.enforced === 'boolean' ? payload.write_auth.enforced : null);
        setWriteAuthHeader(payload.write_auth?.header || 'X-Admin-Key');
        setWriteAuthSessionHeader(payload.write_auth?.session_header || 'X-Admin-Session');
        setWriteAuthKeyConfigured(
          typeof payload.write_auth?.admin_key_configured === 'boolean'
            ? payload.write_auth.admin_key_configured
            : null,
        );
      } catch {
        if (cancelled) return;
        setHealthStatus('API check failed');
        setPublicReadonlyMode(null);
        setWriteAuthEnforced(null);
        setWriteAuthSessionHeader('X-Admin-Session');
        setWriteAuthKeyConfigured(null);
      }
    }

    void run();
    refreshAdminSessionState();
    return () => {
      cancelled = true;
    };
  }, [refreshAdminSessionState]);

  useEffect(() => {
    refreshAdminSessionState();
    const timer = window.setInterval(() => {
      refreshAdminSessionState();
    }, 30000);
    return () => window.clearInterval(timer);
  }, [refreshAdminSessionState]);

  useEffect(() => {
    refreshTutorialProgress();
    function onTutorialProgress() {
      refreshTutorialProgress();
    }
    window.addEventListener(SCOUTING_TUTORIAL_PROGRESS_EVENT, onTutorialProgress as EventListener);
    return () => window.removeEventListener(SCOUTING_TUTORIAL_PROGRESS_EVENT, onTutorialProgress as EventListener);
  }, []);

  useEffect(() => {
    let cancelled = false;
    setOpsLoading(true);
    setOpsErrorText('');
    void getOpsDashboard(14)
      .then((payload) => {
        if (cancelled) return;
        setOpsDashboard(payload);
      })
      .catch((error) => {
        if (cancelled) return;
        setOpsDashboard(null);
        setOpsErrorText((error as Error).message || 'Unable to load ops dashboard.');
      })
      .finally(() => {
        if (!cancelled) setOpsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  function persistSettings(partial?: {
    theme?: ThemeMode;
    density?: DensityMode;
    uiMode?: UIMode;
    quickJumpMode?: QuickJumpMode;
    quickJumpRegion?: QuickJumpRegion;
    liveRefreshSec?: number;
    tutorialAutoplay?: boolean;
  }) {
    const next = saveStoredSettings({
      theme: partial?.theme ?? themeMode,
      density: partial?.density ?? densityMode,
      uiMode: partial?.uiMode ?? uiMode,
      quickJumpMode: partial?.quickJumpMode ?? quickJumpMode,
      quickJumpRegion: partial?.quickJumpRegion ?? quickJumpRegion,
      liveRefreshSec: partial?.liveRefreshSec ?? liveRefreshSec,
      tutorialAutoplay: partial?.tutorialAutoplay ?? tutorialAutoplay,
    });
    applyBodySettingsClasses(next);
    emitSettingsUpdated(next);
    setLastSavedAt(Date.now());
    setStatusText('Settings saved.');
  }

  function updateTheme(next: ThemeMode) {
    setThemeMode(next);
    persistSettings({ theme: next });
  }

  function updateDensity(next: DensityMode) {
    setDensityMode(next);
    persistSettings({ density: next });
  }

  function updateUiMode(next: UIMode) {
    setUiMode(next);
    persistSettings({ uiMode: next });
  }

  function updateQuickJumpMode(next: QuickJumpMode) {
    setQuickJumpMode(next);
    persistSettings({ quickJumpMode: next });
  }

  function updateQuickJumpRegion(next: QuickJumpRegion) {
    setQuickJumpRegion(next);
    persistSettings({ quickJumpRegion: next });
  }

  function updateLiveRefreshSec(next: number) {
    const normalized = Math.max(5, Math.min(120, Math.round(next)));
    setLiveRefreshSec(normalized);
    persistSettings({ liveRefreshSec: normalized });
  }

  function updateTutorialAutoplay(next: boolean) {
    setTutorialAutoplay(next);
    persistSettings({ tutorialAutoplay: next });
    setStatusText(next ? 'Tutorial autoplay enabled for unseen tabs.' : 'Tutorial autoplay disabled.');
  }

  async function validateAndEnableAdminMode() {
    const candidate = adminApiKeyInput.trim();
    if (!candidate) {
      setAdminAuthStatus('Enter an admin API key first.');
      setAdminAuthStatusTone('warning');
      return;
    }
    setAdminAuthBusy(true);
    setAdminAuthStatus('Creating admin session...');
    setAdminAuthStatusTone('muted');
    try {
      const result = await createAdminSession(candidate, 7200);
      if (result.ok && result.authorized && result.session_token) {
        setStoredClientAdminSessionToken(result.session_token, result.expires_at_unix);
        setClientAdminModeEnabled(true);
        setAdminModeEnabled(true);
        setAdminSessionExpiresAtUnix(result.expires_at_unix || null);
        setAdminApiKeyInput('');
        setAdminAuthStatus('Admin session created successfully.');
        setAdminAuthStatusTone('success');
        setStatusText('Admin mode enabled with short-lived session token.');
        setLastSavedAt(Date.now());
        emitSettingsUpdated();
        refreshAdminSessionState();
      } else {
        clearStoredClientAdminSessionToken();
        setClientAdminModeEnabled(false);
        setAdminModeEnabled(false);
        if (result.status === 401 || result.status === 403) {
          setAdminAuthStatus('Admin mode failed: invalid admin API key or expired credentials.');
        } else if (result.status >= 500) {
          setAdminAuthStatus(`Admin mode failed: backend error (${result.status}).`);
        } else {
          setAdminAuthStatus(result.detail || `Admin mode failed (status ${result.status}).`);
        }
        setAdminAuthStatusTone('danger');
        setStatusText('Admin mode remains disabled.');
      }
    } catch (error) {
      const detail = (error as Error).message || 'Admin key validation failed.';
      const connectionIssue = /(failed to fetch|network|timeout|timed out|abort)/i.test(detail);
      clearStoredClientAdminSessionToken();
      setClientAdminModeEnabled(false);
      setAdminModeEnabled(false);
      setAdminAuthStatus(
        connectionIssue ? 'Admin mode failed: could not connect to backend for session creation.' : detail,
      );
      setAdminAuthStatusTone('danger');
      setStatusText('Admin mode remains disabled.');
    } finally {
      setAdminAuthBusy(false);
    }
  }

  function disableAdminMode() {
    clearStoredClientAdminSessionToken();
    setClientAdminModeEnabled(false);
    setAdminModeEnabled(false);
    setAdminSessionExpiresAtUnix(null);
    setAdminAuthStatus('Admin mode disabled.');
    setAdminAuthStatusTone('muted');
    setStatusText('Admin mode disabled for this browser.');
    setLastSavedAt(Date.now());
    emitSettingsUpdated();
  }

  function clearAdminModeKey() {
    clearStoredClientAdminSessionToken();
    setClientAdminModeEnabled(false);
    setAdminApiKeyInput('');
    setAdminModeEnabled(false);
    setAdminSessionExpiresAtUnix(null);
    setAdminAuthStatus('Saved admin session token removed.');
    setAdminAuthStatusTone('muted');
    setStatusText('Saved admin session token removed from this browser.');
    setLastSavedAt(Date.now());
    emitSettingsUpdated();
  }

  function clearFavorites() {
    window.localStorage.removeItem(FAVORITES_KEYS.events);
    window.localStorage.removeItem(FAVORITES_KEYS.teams);
    refreshDiagnostics();
    setStatusText('Favorite teams/events cleared.');
    setLastSavedAt(Date.now());
  }

  function clearCompareCache() {
    window.localStorage.removeItem(COMPARE_KEYS.event);
    window.localStorage.removeItem(COMPARE_KEYS.teams);
    refreshDiagnostics();
    setStatusText('Compare cache cleared.');
    setLastSavedAt(Date.now());
  }

  function resetTutorialProgress() {
    clearTutorialSeen();
    clearTutorialChecklist();
    refreshTutorialProgress();
    setStatusText('Tutorial progress cleared. Tutorials can autoplay again.');
    setLastSavedAt(Date.now());
  }

  function resetDefaults() {
    const defaults = saveStoredSettings({
      theme: 'dark',
      density: 'comfortable',
      uiMode: 'full',
      quickJumpMode: 'auto',
      quickJumpRegion: 'all',
      liveRefreshSec: 60,
      tutorialAutoplay: true,
    });
    setThemeMode(defaults.theme);
    setDensityMode(defaults.density);
    setUiMode(defaults.uiMode);
    setQuickJumpMode(defaults.quickJumpMode);
    setQuickJumpRegion(defaults.quickJumpRegion);
    setLiveRefreshSec(defaults.liveRefreshSec);
    setTutorialAutoplay(defaults.tutorialAutoplay);
    applyBodySettingsClasses(defaults);
    emitSettingsUpdated(defaults);
    setStatusText('Settings reset to defaults.');
    setLastSavedAt(Date.now());
  }
  const statusToneClass =
    adminAuthStatusTone === 'success'
      ? styles.statusOk
      : adminAuthStatusTone === 'warning'
        ? styles.statusWarning
        : adminAuthStatusTone === 'danger'
          ? styles.statusDanger
          : undefined;

  return (
    <div className={styles.layout}>
      <SurfaceCard
        title="Appearance"
        right={<Chip>Saved {relativeFromTimestamp(lastSavedAt)}</Chip>}
      >
        <CardBody>
          <div className={styles.optionGrid}>
            <FieldSelect
              label="Theme"
              value={themeMode}
              onChange={(event) => updateTheme(event.target.value as ThemeMode)}
            >
              <option value="dark">Dark</option>
              <option value="light">Light</option>
            </FieldSelect>

            <FieldSelect
              label="Density"
              value={densityMode}
              onChange={(event) => updateDensity(event.target.value as DensityMode)}
            >
              <option value="comfortable">Comfortable</option>
              <option value="compact">Compact</option>
            </FieldSelect>
          </div>
          <p className={styles.note}>Applied immediately.</p>
        </CardBody>
      </SurfaceCard>

      <SurfaceCard title="Workflow Defaults">
        <CardBody>
          <div className={styles.optionGrid}>
            <FieldSelect
              label="UI Mode"
              value={uiMode}
              onChange={(event) => updateUiMode(event.target.value as UIMode)}
            >
              <option value="full">Full Diagnostics</option>
              <option value="simple">Simple</option>
            </FieldSelect>

            <FieldSelect
              label="Quick Search Default"
              value={quickJumpMode}
              onChange={(event) => updateQuickJumpMode(event.target.value as QuickJumpMode)}
            >
              <option value="auto">Auto</option>
              <option value="team">Team</option>
              <option value="event">Event</option>
            </FieldSelect>

            <FieldSelect
              label="Region Filter Default"
              value={quickJumpRegion}
              onChange={(event) => updateQuickJumpRegion(event.target.value as QuickJumpRegion)}
            >
              {REGION_FILTER_OPTIONS.map((option) => (
                <option key={`settings-region-${option.value}`} value={option.value}>
                  {option.label}
                </option>
              ))}
            </FieldSelect>

            <FieldSelect
              label="Tutorial Auto-Play"
              value={tutorialAutoplay ? 'enabled' : 'disabled'}
              onChange={(event) => updateTutorialAutoplay(event.target.value === 'enabled')}
            >
              <option value="enabled">Enabled (show once per tab)</option>
              <option value="disabled">Disabled</option>
            </FieldSelect>
          </div>

          <FieldStepper
            label="Live Refresh Interval"
            name="live refresh interval"
            hint="Seconds between polls. Longer intervals survive venue wifi better."
            value={liveRefreshSec}
            onValueChange={updateLiveRefreshSec}
            min={5}
            max={120}
            step={5}
          />

          <p className={styles.note}>
            Tutorials completed: {tutorialSeenCount}/{TUTORIAL_SCOPES.length}.
          </p>
          <div className={styles.actions}>
            <Button variant="quiet" onClick={resetTutorialProgress}>
              Reset Tutorial Progress
            </Button>
          </div>
        </CardBody>
      </SurfaceCard>

      <PushNotificationsCard />

      {adminModeEnabled ? (
      <SurfaceCard title="Refresh + Runtime">
        <CardBody>
          <FieldText
            label="Admin API Key (session mint only)"
            type="password"
            autoComplete="off"
            spellCheck={false}
            value={adminApiKeyInput}
            onChange={(event) => setAdminApiKeyInput(event.target.value)}
            placeholder={`Paste ${writeAuthHeader} to mint ${writeAuthSessionHeader}`}
          />
          <div className={styles.actions}>
            <Button variant="primary" onClick={() => void validateAndEnableAdminMode()} loading={adminAuthBusy}>
              {adminAuthBusy ? 'Creating Session...' : 'Create Admin Session'}
            </Button>
            <Button variant="quiet" onClick={disableAdminMode} disabled={adminAuthBusy}>
              Disable Admin Mode
            </Button>
            <Button variant="quiet" onClick={clearAdminModeKey} disabled={adminAuthBusy}>
              Clear Saved Session
            </Button>
          </div>
          <p className={`${styles.status} ${statusToneClass ?? ''}`.trim()} aria-live="polite">
            {adminAuthStatus}
          </p>
          <div className={styles.chipRow}>
            <Chip>API: {healthStatus}</Chip>
            <Chip>
              Public mode: {publicReadonlyMode === null ? 'unknown' : publicReadonlyMode ? 'read-only' : 'full'}
            </Chip>
            <Chip tone={writeAuthEnforced ? 'accent' : 'warn'}>
              Write auth: {writeAuthEnforced === null ? 'unknown' : writeAuthEnforced ? 'enforced' : 'not enforced'}
            </Chip>
            <Chip tone={writeAuthKeyConfigured ? 'accent' : 'warn'}>
              Admin key: {writeAuthKeyConfigured === null ? 'unknown' : writeAuthKeyConfigured ? 'configured' : 'missing'}
            </Chip>
            <Chip tone={adminModeEnabled ? 'accent' : 'neutral'}>
              Admin mode: {adminModeEnabled ? 'enabled' : 'disabled'}
            </Chip>
            <Chip tone={clientAdminKeyAvailable() ? 'accent' : 'neutral'} dot>
              Admin session: {clientAdminKeyAvailable() ? 'active' : 'inactive'}
            </Chip>
            <Chip>Header: {writeAuthHeader}</Chip>
            <Chip>Session header: {writeAuthSessionHeader}</Chip>
            <Chip>
              Session expires:{' '}
              {adminSessionExpiresAtUnix && adminSessionExpiresAtUnix > 0
                ? relativeFromTimestamp(adminSessionExpiresAtUnix * 1000)
                : 'N/A'}
            </Chip>
            <Chip>Status: {statusText}</Chip>
          </div>
        </CardBody>
      </SurfaceCard>
      ) : null}

      {adminModeEnabled ? (
      <>
      <SurfaceCard title="Ops Dashboard">
        <CardBody>
          <div className={styles.actions}>
            <Button variant="quiet" onClick={() => void refreshOpsDashboard()} loading={opsLoading}>
              {opsLoading ? 'Refreshing...' : 'Refresh Ops Snapshot'}
            </Button>
            <Button variant="quiet" onClick={() => void runClimbBackfillNow()} loading={climbBackfillBusy}>
              {climbBackfillBusy ? 'Backfilling...' : 'Run Climb Backfill'}
            </Button>
          </div>
          {opsErrorText ? (
            <p className={`${styles.note} ${styles.noteWarning}`} role="alert">{opsErrorText}</p>
          ) : null}
          {climbBackfillStatus ? <p className={styles.note}>{climbBackfillStatus}</p> : null}
          {opsLoading && !opsDashboard ? <p className={styles.note}>Loading...</p> : null}
          {opsDashboard ? (
            <>
              <CardGrid dense>
                <Stat label="Alerts" value={opsDashboard.alert_count} tone={opsDashboard.alert_count > 0 ? 'danger' : 'success'} />
                <Stat label="Queue Pending" value={opsDashboard.queue.counts?.pending_total ?? 'N/A'} />
                <Stat
                  label="Queue Pressure"
                  value={
                    typeof opsDashboard.queue.pressure_0_1 === 'number'
                      ? Math.round(opsDashboard.queue.pressure_0_1 * 100)
                      : 'N/A'
                  }
                  unit={typeof opsDashboard.queue.pressure_0_1 === 'number' ? '%' : undefined}
                />
                <Stat
                  label="Regional Auto"
                  value={
                    opsDashboard.automation.enabled
                      ? opsDashboard.automation.locked
                        ? 'Locked'
                        : opsDashboard.automation.due_now
                          ? 'Due'
                          : 'Idle'
                      : 'Disabled'
                  }
                />
                <Stat
                  label="Media Used"
                  value={
                    typeof opsDashboard.media_usage.total_gb === 'number'
                      ? opsDashboard.media_usage.total_gb.toFixed(2)
                      : 'N/A'
                  }
                  unit={typeof opsDashboard.media_usage.total_gb === 'number' ? 'GB' : undefined}
                />
                <Stat
                  label="Disk Free"
                  value={
                    typeof opsDashboard.media_usage.disk_free_gb === 'number'
                      ? opsDashboard.media_usage.disk_free_gb.toFixed(2)
                      : 'N/A'
                  }
                  unit={typeof opsDashboard.media_usage.disk_free_gb === 'number' ? 'GB' : undefined}
                />
                <Stat
                  label="Climb Audit"
                  value={
                    opsDashboard.climb_integrity.last_result?.severity
                      ? String(opsDashboard.climb_integrity.last_result.severity).toUpperCase()
                      : 'N/A'
                  }
                />
                <Stat
                  label="Climb Mismatch"
                  value={
                    typeof opsDashboard.climb_integrity.last_result?.totals?.mismatch_rate === 'number'
                      ? Math.round((opsDashboard.climb_integrity.last_result?.totals?.mismatch_rate || 0) * 100)
                      : 'N/A'
                  }
                  unit={
                    typeof opsDashboard.climb_integrity.last_result?.totals?.mismatch_rate === 'number'
                      ? '%'
                      : undefined
                  }
                />
                <Stat
                  label="Climb Signal Coverage"
                  value={
                    typeof opsDashboard.climb_signal_coverage?.summary?.coverage_any_pct === 'number'
                      ? opsDashboard.climb_signal_coverage.summary.coverage_any_pct.toFixed(1)
                      : 'N/A'
                  }
                  unit={
                    typeof opsDashboard.climb_signal_coverage?.summary?.coverage_any_pct === 'number'
                      ? '%'
                      : undefined
                  }
                />
                <Stat
                  label="Official Climb Coverage"
                  value={
                    typeof opsDashboard.climb_signal_coverage?.summary?.coverage_official_pct === 'number'
                      ? opsDashboard.climb_signal_coverage.summary.coverage_official_pct.toFixed(1)
                      : 'N/A'
                  }
                  unit={
                    typeof opsDashboard.climb_signal_coverage?.summary?.coverage_official_pct === 'number'
                      ? '%'
                      : undefined
                  }
                />
              </CardGrid>
              <div className={styles.chipRow}>
                <Chip>Generated: {relativeFromTimestamp(Date.parse(opsDashboard.generated_at))}</Chip>
                <Chip>Queue cap: {opsDashboard.queue.caps?.max_pending_jobs ?? 'N/A'}</Chip>
                <Chip>Automation season: {opsDashboard.automation.season}</Chip>
              </div>
            </>
          ) : null}
        </CardBody>
      </SurfaceCard>

      <SurfaceCard title="Local Diagnostics">
        <CardBody>
          <CardGrid dense>
            <Stat label="Favorite Events" value={diagnostics.favoriteEventsCount} />
            <Stat label="Favorite Teams" value={diagnostics.favoriteTeamsCount} />
            <Stat label="Compare Event" value={diagnostics.compareEventKey || 'None'} />
            <Stat label="Compare Teams" value={diagnostics.compareTeamCount} />
          </CardGrid>

          <div className={styles.actions}>
            <Button variant="quiet" onClick={refreshDiagnostics}>
              Refresh Diagnostics
            </Button>
            <Button as="a" variant="quiet" href="#/favorites" title="Open Favorites page">
              Favorites
            </Button>
            <Button as="a" variant="quiet" href="#/compare" title="Open Compare page">
              Compare
            </Button>
          </div>
        </CardBody>
      </SurfaceCard>
      </>
      ) : null}

      {/* Admin tools toggle */}
      <div className={styles.adminToggle}>
        <Button
          variant="quiet"
          pressed={adminModeEnabled}
          onClick={() => {
            const next = !adminModeEnabled;
            setClientAdminModeEnabled(next);
            setAdminModeEnabled(next);
            refreshAdminSessionState();
          }}
        >
          {adminModeEnabled ? 'Hide Admin Tools' : 'Show Admin Tools'}
        </Button>
      </div>

      <SurfaceCard title="Maintenance">
        <CardBody>
          <div className={styles.actions}>
            <Button variant="quiet" onClick={clearFavorites}>
              Clear Favorites
            </Button>
            <Button variant="quiet" onClick={clearCompareCache}>
              Clear Compare Cache
            </Button>
            <Button variant="danger" onClick={resetDefaults}>
              Reset Defaults
            </Button>
          </div>
          <p className={styles.note}>Only clears local browser state.</p>
        </CardBody>
      </SurfaceCard>

      <SurfaceCard
        title="About & Legal"
        subtitle="How FRCMOB handles your data, and the terms of use."
      >
        <CardBody>
          <div className={styles.actions}>
            <Button as="a" variant="quiet" href="#/privacy" title="Read the Privacy Policy">
              Privacy Policy
            </Button>
            <Button as="a" variant="quiet" href="#/terms" title="Read the Terms of Service">
              Terms of Service
            </Button>
          </div>
          <p className={styles.note}>
            Recorded video is processed on your device and never uploaded.
          </p>
          <p className={styles.credit}>
            Built by{' '}
            <a href={AUTHOR_SITE_URL} target="_blank" rel="noopener noreferrer">
              {AUTHOR_NAME}
            </a>
            .
          </p>
        </CardBody>
      </SurfaceCard>
    </div>
  );
}
