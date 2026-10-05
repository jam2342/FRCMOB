// Regressions from the 2026-10-04 QA pass: room snapshots must not drop entries saved on the phone.
import { describe, expect, it } from 'vitest';
import { entriesFromRoomRecords, mergeEntries, normalizeEntry, replaceRoomEntries, stripEntriesForRoom } from './scoutingPage.helpers';

describe('QA: reconnect room snapshots preserve unsynced scouting', () => {
  it('retains a phone-only room entry when the server snapshot has not received it', () => {
    const entry = normalizeEntry({ id: 'offline-1', room_key: 'qa-room', event_key: '2026arc', match_key: '2026arc_qm1',
      team_key: 'frc254', scout_profile: 'Scout', saved_at_ms: 123, form: { teleop_scored: 42 } });
    expect(entry).not.toBeNull();
    // Exact reconciliation used by the snapshot, HTTP polling and room rejoin paths.
    const snapshot = entriesFromRoomRecords([], 'qa-room');
    const reconciled = mergeEntries(stripEntriesForRoom([entry!], 'qa-room'), snapshot);
    expect(reconciled.map(row => row.id)).toContain('offline-1');
  });
});

describe('room snapshot replacement', () => {
  const make = (id: string, extra: Record<string, unknown> = {}) => normalizeEntry({ id, room_key: 'qa-room', event_key: '2026arc',
    match_key: '2026arc_qm1', team_key: 'frc254', scout_profile: 'Scout', saved_at_ms: 100, form: {}, ...extra })!;

  it('replaces server copies, keeps phone-only ones, and never duplicates an echoed entry', () => {
    const synced = make('server-1', { server_synced: true });
    const pending = make('pending-1');
    const echoed = make('pending-2');
    const snapshot = entriesFromRoomRecords([
      { room_key: 'qa-room', entry: { ...make('pending-2'), saved_at_ms: 200 } },
    ] as never, 'qa-room');
    const result = replaceRoomEntries([synced, pending, echoed], 'qa-room', snapshot);
    expect(result.map((row) => row.id).sort()).toEqual(['pending-1', 'pending-2']);
    expect(result.find((row) => row.id === 'pending-2')?.server_synced).toBe(true);
  });
});
