import { useEffect, useRef, useState, type ReactNode } from 'react';
import styles from './OfflineIndicator.module.css';
import { useHref, useLinkClickHandler } from 'react-router-dom';
import { AlertTriangleIcon, ClockIcon, CloudSyncIcon, WifiOffIcon } from './Icons';
import { Button } from './primitives';
import { cx } from './primitives/cx';
import { clearSyncIssue, lastSyncIssue } from '../../features/offline/syncReceipt';
import { useOnlineStatus } from '../../hooks/useOnlineStatus';
import { flush, type DroppedMutationInfo } from '../../utils/offlineQueue';
import { downloadCsv } from '../../utils/csvExport';

const SCOUTING_ENTRIES_STORAGE = 'scouting_manual_entries_v2';

function asRecord(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return {};
  return value as Record<string, unknown>;
}

function csvCell(value: unknown): unknown {
  return value ?? '';
}

function formatSavedAt(value: unknown): string {
  if (typeof value !== 'number' && typeof value !== 'string') return '';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '' : date.toISOString();
}

function downloadLocalEntriesCsv() {
  let entries: unknown;
  try {
    entries = JSON.parse(window.localStorage.getItem(SCOUTING_ENTRIES_STORAGE) ?? '[]');
  } catch {
    return;
  }
  if (!Array.isArray(entries) || entries.length === 0) return;

  const headers = [
    'id', 'saved_at', 'scout_profile', 'event_key', 'match_key', 'match_display',
    'team_key', 'team_label', 'alliance', 'station', 'entry_source', 'notes',
    'points_auto', 'points_teleop', 'points_endgame', 'points_total',
    'auto_mobility', 'auto_scored', 'auto_missed', 'auto_cycles',
    'teleop_scored', 'teleop_missed', 'teleop_cycles', 'teleop_drops',
    'endgame_mode', 'foul_count',
    'driver_score_0_100', 'manual_rating_score_0_100', 'overall_scout_score_0_100',
  ];
  const rows: unknown[][] = entries.map((entry) => {
    const e = asRecord(entry);
    const points = asRecord(e['points']);
    const form = asRecord(e['form']);
    const driverCompetency = asRecord(e['driver_competency']);
    const manualRating = asRecord(e['manual_rating']);
    const overallScoutRating = asRecord(e['overall_scout_rating']);

    return [
      csvCell(e['id']),
      formatSavedAt(e['saved_at_ms']),
      csvCell(e['scout_profile']),
      csvCell(e['event_key']),
      csvCell(e['match_key']),
      csvCell(e['match_display']),
      csvCell(e['team_key']),
      csvCell(e['team_label']),
      csvCell(e['alliance']),
      csvCell(e['station']),
      e['entry_source'] ?? 'manual',
      csvCell(e['notes']),
      csvCell(points['auto']),
      csvCell(points['teleop']),
      csvCell(points['endgame']),
      csvCell(points['total']),
      csvCell(form['auto_mobility']),
      csvCell(form['auto_scored']),
      csvCell(form['auto_missed']),
      csvCell(form['auto_cycles']),
      csvCell(form['teleop_scored']),
      csvCell(form['teleop_missed']),
      csvCell(form['teleop_cycles']),
      csvCell(form['teleop_drops']),
      csvCell(form['endgame_mode']),
      csvCell(form['foul_count']),
      csvCell(driverCompetency['score_0_100']),
      csvCell(manualRating['score_0_100']),
      csvCell(overallScoutRating['score_0_100']),
    ];
  });

  const date = new Date().toISOString().slice(0, 10);
  void downloadCsv(`scouting-entries-offline-${date}.csv`, headers, rows).catch(() => window.alert('The export could not be saved. Your scouting entries are still on this phone.'));
}

/**
 * Small banner shown at the top of the app when the device is offline,
 * and/or when there are queued mutations waiting to sync.
 */
// After a spell offline: what was waiting visibly goes out ("sharing 2 of 3"), then one clear
// "all synced" that folds away, instead of the offline banner just vanishing.
type Reconnect = { total: number; phase: 'syncing' | 'done' | 'leaving' };
const DONE_MS = 3000;
const BACK_ONLINE_MS = 1800;
const LEAVE_MS = 260;

function useReconnectMoment(online: boolean, queueSize: number): Reconnect | null {
  const [moment, setMoment] = useState<Reconnect | null>(null);
  const wasOnline = useRef(online);

  useEffect(() => {
    const before = wasOnline.current;
    wasOnline.current = online;
    if (!online) {
      setMoment(null);
      return;
    }
    if (!before) setMoment(queueSize > 0 ? { total: queueSize, phase: 'syncing' } : { total: 0, phase: 'done' });
  }, [online, queueSize]);

  useEffect(() => {
    if (!moment || moment.phase !== 'syncing') return;
    if (queueSize === 0) setMoment({ ...moment, phase: 'done' });
    else if (queueSize > moment.total) setMoment({ ...moment, total: queueSize });
  }, [moment, queueSize]);

  useEffect(() => {
    if (!moment || moment.phase === 'syncing') return undefined;
    const timer = window.setTimeout(
      () => setMoment(moment.phase === 'done' ? { ...moment, phase: 'leaving' } : null),
      moment.phase === 'done' ? (moment.total > 0 ? DONE_MS : BACK_ONLINE_MS) : LEAVE_MS,
    );
    return () => window.clearTimeout(timer);
  }, [moment]);

  return moment;
}

type Tone = 'neutral' | 'warning' | 'danger';

// Every state is one line of text: icon, short status, quiet detail, with any buttons after it.
// It wraps as a sentence on a narrow phone instead of splitting into columns. `offline-banner`
// stays on the root because the shell measures that class to keep sticky bars clear of it.
function Notice({
  tone = 'neutral',
  icon,
  actions,
  className,
  children,
  ...live
}: {
  tone?: Tone;
  icon: ReactNode;
  actions?: ReactNode;
  className?: string;
  children: ReactNode;
  role: 'status' | 'alert';
  'aria-live'?: 'polite';
}) {
  return (
    <div {...live} className={cx('offline-banner', styles.notice, styles[tone], className)}>
      <p className={styles.text}>
        <span className={styles.icon} aria-hidden="true">{icon}</span>
        {children}
      </p>
      {actions ? <div className={styles.actions}>{actions}</div> : null}
    </div>
  );
}

function Detail({ children }: { children: ReactNode }) {
  return (
    <>
      <span className={styles.sep} aria-hidden="true"> · </span>
      <span className={styles.hint}>{children}</span>
    </>
  );
}

// A link that looks like the notice's other buttons. Its own component because useHref needs a
// router, and only this state links anywhere.
function RecoverLink() {
  const href = useHref('/my-team');
  const onClick = useLinkClickHandler('/my-team');
  return (
    <Button as="a" variant="quiet" size="sm" href={href} onClick={onClick}>
      Recover saved edits
    </Button>
  );
}

// The shell's "these numbers are from the saved copy" notice: the same notice, so the two never
// sit one above the other in two different looks.
export function StaleDataNotice() {
  return (
    <Notice role="status" aria-live="polite" icon={<ClockIcon size={16} />}>
      <span className={styles.title}>Showing data from last session</span>
    </Notice>
  );
}

export function OfflineIndicator() {
  const { online, queueSize, serverTrouble } = useOnlineStatus();
  const reconnect = useReconnectMoment(online, queueSize);
  const [dropped, setDropped] = useState<DroppedMutationInfo | null>(() => {
    const issue = lastSyncIssue();
    return issue ? { reason: issue.reason as DroppedMutationInfo['reason'], count: issue.count, labels: issue.labels } : null;
  });

  useEffect(() => {
    const onDropped = (e: Event) => {
      const detail = (e as CustomEvent<DroppedMutationInfo>).detail;
      if (detail && detail.count > 0) setDropped(detail);
    };
    window.addEventListener('offlinequeue:dropped', onDropped);
    return () => window.removeEventListener('offlinequeue:dropped', onDropped);
  }, []);

  if (dropped) {
    return (
      <Notice
        role="alert"
        tone="danger"
        icon={<AlertTriangleIcon size={16} />}
        actions={
          <>
            {dropped.reason === 'storage-failure' ? (
              <Button variant="quiet" size="sm" onClick={downloadLocalEntriesCsv}>Download local CSV</Button>
            ) : (
              <RecoverLink />
            )}
            <Button variant="quiet" size="sm" onClick={() => { clearSyncIssue(); setDropped(null); }}>
              Dismiss
            </Button>
          </>
        }
      >
        {dropped.count} queued change{dropped.count > 1 ? 's' : ''}{' '}
        {dropped.reason === 'conflict'
          ? "conflicted with a teammate; your original edit is still saved on this phone"
          : dropped.reason === 'server-rejected'
            ? 'were rejected by the server; your original edit is still saved on this phone'
            : 'could not be saved for offline sync'}
        {dropped.labels.length > 0 ? ` (${dropped.labels.slice(0, 3).join(', ')}${dropped.labels.length > 3 ? ', …' : ''})` : ''}
      </Notice>
    );
  }

  if (online && reconnect) {
    const synced = Math.max(0, reconnect.total - queueSize);
    const syncing = reconnect.phase === 'syncing';
    const message = syncing
      ? `Back online · sharing ${Math.min(synced + 1, reconnect.total)} of ${reconnect.total} change${reconnect.total === 1 ? '' : 's'}`
      : reconnect.total > 0
        ? `All ${reconnect.total} change${reconnect.total === 1 ? '' : 's'} synced`
        : 'Back online';
    return (
      <Notice
        role="status"
        aria-live="polite"
        className={cx(syncing ? styles.syncing : styles.success, reconnect.phase === 'leaving' && styles.leaving)}
        icon={
          syncing ? (
            <svg className={styles.spin} viewBox="0 0 24 24" width="16" height="16">
              <path d="M12 3a9 9 0 1 0 9 9" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
            </svg>
          ) : (
            <svg className={styles.check} viewBox="0 0 24 24" width="16" height="16">
              <path d="M5 12.5l4.5 4.5L19 7.5" fill="none" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          )
        }
      >
        <span className={styles.title}>{message}</span>
        {syncing && reconnect.total > 0 ? (
          <span className={styles.progress} aria-hidden="true">
            <span style={{ transform: `scaleX(${synced / reconnect.total})` }} />
          </span>
        ) : null}
      </Notice>
    );
  }

  if (online && queueSize === 0) return null;

  const handleFlush = () => {
    flush().catch(() => {});
  };

  if (online) {
    // Pending changes while online is the state that wants attention: there is an action to
    // take, and leaving the venue without taking it loses work. So it is the one warning tint.
    return (
      <Notice
        role="status"
        aria-live="polite"
        tone="warning"
        icon={<CloudSyncIcon size={16} />}
        actions={<Button variant="quiet" size="sm" onClick={handleFlush}>Sync now</Button>}
      >
        <span className={styles.title}>{`${queueSize} pending change${queueSize > 1 ? 's' : ''} to sync`}</span>
      </Notice>
    );
  }

  const localEntryCount = (() => {
    try {
      const raw = window.localStorage.getItem(SCOUTING_ENTRIES_STORAGE);
      const arr = JSON.parse(raw ?? '[]');
      return Array.isArray(arr) ? arr.length : 0;
    } catch {
      return 0;
    }
  })();

  // Offline is the state this app is built for (venue wifi), and the copy says the data is
  // safe, so it stays neutral: no alarm tint, and no green, which means live or selected.
  return (
    <Notice
      role="status"
      aria-live="polite"
      icon={serverTrouble ? <AlertTriangleIcon size={16} /> : <WifiOffIcon size={16} />}
      actions={
        localEntryCount > 0 ? (
          <Button variant="quiet" size="sm" onClick={downloadLocalEntriesCsv}>Download CSV</Button>
        ) : null
      }
    >
      <span className={styles.title}>{serverTrouble ? "Can't reach FRCMOB's server right now" : 'You are offline'}</span>
      {/* What's waiting is the number that matters offline. The local entry count is every
          entry this phone has ever kept (up to 600), which read like a sync backlog when it
          wasn't one. */}
      <Detail>
        {queueSize > 0
          ? `${queueSize} change${queueSize === 1 ? '' : 's'} will sync when you're back online`
          : 'Anything you save stays on this phone'}
      </Detail>
    </Notice>
  );
}
