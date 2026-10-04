import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
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
export function OfflineIndicator() {
  const { online, queueSize } = useOnlineStatus();
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
      <div role="alert" className="offline-banner offline-banner-danger">
        <span className="offline-banner-status">
          <span className="offline-banner-dot offline-dot-danger" />
          {dropped.count} queued change{dropped.count > 1 ? 's' : ''}{' '}
          {dropped.reason === 'conflict'
            ? "conflicted with a teammate; your original edit is still saved on this phone"
            : dropped.reason === 'server-rejected'
              ? 'were rejected by the server; your original edit is still saved on this phone'
              : 'could not be saved for offline sync'}
          {dropped.labels.length > 0 ? ` (${dropped.labels.slice(0, 3).join(', ')}${dropped.labels.length > 3 ? ', …' : ''})` : ''}
        </span>
        {dropped.reason === 'storage-failure' ? (
          <button onClick={downloadLocalEntriesCsv} className="offline-banner-sync-btn">Download local CSV</button>
        ) : (
          <Link to="/my-team" className="offline-banner-sync-btn">Recover saved edits</Link>
        )}
        <button onClick={() => { clearSyncIssue(); setDropped(null); }} className="offline-banner-sync-btn">
          Dismiss
        </button>
      </div>
    );
  }

  if (online && queueSize === 0) return null;

  const handleFlush = () => {
    flush().catch(() => {});
  };

  const localEntryCount = (() => {
    try {
      const raw = window.localStorage.getItem(SCOUTING_ENTRIES_STORAGE);
      const arr = JSON.parse(raw ?? '[]');
      return Array.isArray(arr) ? arr.length : 0;
    } catch {
      return 0;
    }
  })();

  return (
    <div
      role="status"
      aria-live="polite"
      className={`offline-banner ${online ? 'offline-banner-warning' : 'offline-banner-info'}`}
    >
      <span className="offline-banner-status">
        <span className={`offline-banner-dot ${online ? 'offline-dot-warning' : 'offline-dot-info'}`} />
        {!online && 'You are offline'}
        {online && queueSize > 0 && `${queueSize} pending change${queueSize > 1 ? 's' : ''} to sync`}
      </span>
      {online && queueSize > 0 && (
        <button onClick={handleFlush} className="offline-banner-sync-btn">
          Sync now
        </button>
      )}
      {!online && (
        <>
          {/* What's waiting is the number that matters offline. The local
              entry count is every entry this phone has ever kept (up to 600),
              which read like a sync backlog when it wasn't one. */}
          <span className="offline-banner-hint">
            {queueSize > 0
              ? `${queueSize} change${queueSize === 1 ? '' : 's'} will sync when you're back online`
              : 'Anything you save stays on this phone'}
          </span>
          {localEntryCount > 0 && (
            <button onClick={downloadLocalEntriesCsv} className="offline-banner-sync-btn">
              Download CSV
            </button>
          )}
        </>
      )}
    </div>
  );
}
