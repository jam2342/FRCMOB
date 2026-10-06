import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { TeamRoomSnapshot } from '../../api';
import { setWorkspaceSession } from './workspaceSession';
import { signOutTestWorkspace } from '../../test/workspace';
import { useTeamRoom } from './useTeamRoom';

const api = vi.hoisted(() => ({
  ensureTeamRoom: vi.fn(),
  getTeamRoom: vi.fn(),
  saveTeamRoomAssignments: vi.fn(),
  getEventSchedule: vi.fn(),
}));
vi.mock('../../api', () => api);
vi.mock('../../hooks/useOnlineStatus', () => ({
  useOnlineStatus: () => ({ online: true, queueSize: 0, isShowingOfflineData: false }),
}));

function signInAs(memberId: number, role: 'leader' | 'member') {
  setWorkspaceSession({
    token: 'test-workspace-token',
    expiresAt: Math.floor(Date.now() / 1000) + 3600,
    workspace: { id: 1, name: 'Test Team', frc_team_number: 118 },
    me: { id: memberId, display_name: `Member ${memberId}`, role },
  });
}

function snapshot(memberId: number, assigned: number | null): TeamRoomSnapshot {
  return {
    ok: true,
    room_key: 'team-room',
    event_key: '2026week0',
    me: { member_id: memberId, display_name: `Member ${memberId}`, role: memberId === 1 ? 'leader' : 'member' },
    members: [],
    assignments: assigned === null ? [] : [{
      match_key: '2026week0_qm1',
      team_key: 'frc254',
      assigned_member_id: assigned,
      assigned_display_name: `Member ${assigned}`,
      member_active: true,
      covered: false,
      covered_by_me: false,
    }],
  };
}

beforeEach(() => {
  localStorage.clear();
  api.getEventSchedule.mockResolvedValue({ ok: true, event_key: '2026week0', event_name: 'Week 0', matches: [] });
});

afterEach(() => {
  vi.useRealTimers();
  signOutTestWorkspace();
  vi.clearAllMocks();
});

describe('useTeamRoom', () => {
  it('keeps the same snapshot object when a poll brings nothing new', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    signInAs(1, 'leader');
    api.ensureTeamRoom.mockResolvedValue({ ...snapshot(1, 2), created: false });
    api.getTeamRoom.mockImplementation(async () => snapshot(1, 2));
    const { result } = renderHook(() => useTeamRoom('2026week0'));
    await waitFor(() => expect(result.current.status).toBe('ready'));
    const first = result.current.snapshot;
    await act(async () => { vi.advanceTimersByTime(5_100); });
    await act(async () => { vi.advanceTimersByTime(5_100); });
    expect(api.getTeamRoom).toHaveBeenCalled();
    // Same object: React skips re-rendering the (very large) Scouting page.
    expect(result.current.snapshot).toBe(first);
  });

  it('never overlaps a slow poll with another tick or focus refresh', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    signInAs(1, 'leader');
    api.ensureTeamRoom.mockResolvedValue({ ...snapshot(1, 2), created: false });
    let finish!: (value: TeamRoomSnapshot) => void;
    api.getTeamRoom.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    const { result } = renderHook(() => useTeamRoom('2026week0'));
    await waitFor(() => expect(result.current.status).toBe('ready'));
    await act(async () => { vi.advanceTimersByTime(5_100); });
    expect(api.getTeamRoom).toHaveBeenCalledTimes(1);
    await act(async () => {
      vi.advanceTimersByTime(15_000);
      window.dispatchEvent(new Event('focus'));
    });
    expect(api.getTeamRoom).toHaveBeenCalledTimes(1);
    await act(async () => { finish(snapshot(1, 2)); });
    await act(async () => { vi.advanceTimersByTime(5_000); });
    expect(api.getTeamRoom).toHaveBeenCalledTimes(2);
    await act(async () => { finish(snapshot(1, 2)); });
  });

  it('backs off instead of re-creating the room on every poll when it keeps failing', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    signInAs(1, 'leader');
    api.ensureTeamRoom.mockRejectedValue(new Error('Event not found.'));
    api.getTeamRoom.mockResolvedValue(null);
    const { result } = renderHook(() => useTeamRoom('2026week0'));
    await waitFor(() => expect(result.current.status).toBe('error'));
    for (let i = 0; i < 6; i += 1) await act(async () => { vi.advanceTimersByTime(5_000); });
    // 30 s of polling: the first try, then retries at +5 s, +10 s, +20 s at most.
    expect(api.ensureTeamRoom.mock.calls.length).toBeLessThanOrEqual(4);
    expect(api.getTeamRoom).not.toHaveBeenCalled();
  });

  it('runs saves one at a time so the last answer is the newest state', async () => {
    signInAs(1, 'leader');
    api.ensureTeamRoom.mockResolvedValue({ ...snapshot(1, null), created: false });
    api.getTeamRoom.mockResolvedValue(snapshot(1, 2));
    const order: string[] = [];
    let releaseFirst: () => void = () => {};
    api.saveTeamRoomAssignments
      .mockImplementationOnce(() => new Promise((resolve) => {
        order.push('first started');
        releaseFirst = () => resolve(snapshot(1, 1));
      }))
      .mockImplementationOnce(async () => {
        order.push('second started');
        return snapshot(1, 2);
      });

    const { result } = renderHook(() => useTeamRoom('2026week0'));
    await waitFor(() => expect(result.current.status).toBe('ready'));

    let first!: Promise<unknown>;
    let second!: Promise<unknown>;
    act(() => {
      first = result.current.saveChanges([{ match_key: '2026week0_qm1', team_key: 'frc254', assigned_member_id: 1 }]);
      second = result.current.saveChanges([{ match_key: '2026week0_qm1', team_key: 'frc254', assigned_member_id: 2 }]);
    });
    await waitFor(() => expect(order).toEqual(['first started']));
    await act(async () => { releaseFirst(); await first; await second; });
    expect(order).toEqual(['first started', 'second started']);
    await waitFor(() => expect(result.current.snapshot?.assignments[0]?.assigned_member_id).toBe(2));
  });

  it('never lets a poll that started before a save put the old assignments back', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    signInAs(1, 'leader');
    api.ensureTeamRoom.mockResolvedValue({ ...snapshot(1, null), created: false });
    const polls: Array<(value: TeamRoomSnapshot) => void> = [];
    api.getTeamRoom.mockImplementation(() => new Promise((resolve) => { polls.push(resolve); }));
    api.saveTeamRoomAssignments.mockResolvedValue(snapshot(1, 2));

    const { result } = renderHook(() => useTeamRoom('2026week0'));
    await waitFor(() => expect(result.current.snapshot?.assignments).toEqual([]));

    await act(async () => { vi.advanceTimersByTime(5_100); });
    expect(api.getTeamRoom).toHaveBeenCalledTimes(1);

    await act(async () => {
      await result.current.saveChanges([{ match_key: '2026week0_qm1', team_key: 'frc254', assigned_member_id: 2 }]);
    });
    expect(result.current.snapshot?.assignments).toHaveLength(1);

    // The save queues a re-read behind the slow poll, whose old answer is dropped.
    expect(polls).toHaveLength(1);
    await act(async () => { polls[0](snapshot(1, null)); });
    expect(result.current.snapshot?.assignments).toHaveLength(1);
    expect(polls).toHaveLength(2);
    await act(async () => { polls[1](snapshot(1, 2)); });
    expect(result.current.snapshot?.assignments).toHaveLength(1);
  });

  it("doesn't show one member's saved copy to another member on the same phone", async () => {
    signInAs(1, 'leader');
    api.ensureTeamRoom.mockResolvedValueOnce({ ...snapshot(1, 1), created: false });
    api.getTeamRoom.mockResolvedValue(null);
    const first = renderHook(() => useTeamRoom('2026week0'));
    await waitFor(() => expect(first.result.current.snapshot?.me.member_id).toBe(1));
    first.unmount();

    signInAs(2, 'member');
    api.ensureTeamRoom.mockRejectedValueOnce(new Error('No signal'));
    const second = renderHook(() => useTeamRoom('2026week0'));
    await waitFor(() => expect(second.result.current.status).toBe('error'));
    expect(second.result.current.snapshot).toBeNull();
    expect(second.result.current.isLeader).toBe(false);
  });

  it('shows the saved copy to the same member when the network is gone', async () => {
    signInAs(2, 'member');
    api.ensureTeamRoom.mockResolvedValueOnce({ ...snapshot(2, 2), created: false });
    api.getTeamRoom.mockResolvedValue(null);
    const first = renderHook(() => useTeamRoom('2026week0'));
    await waitFor(() => expect(first.result.current.snapshot?.assignments).toHaveLength(1));
    first.unmount();

    api.ensureTeamRoom.mockRejectedValueOnce(new Error('No signal'));
    const second = renderHook(() => useTeamRoom('2026week0'));
    await waitFor(() => expect(second.result.current.status).toBe('error'));
    expect(second.result.current.snapshot?.assignments).toHaveLength(1);
    expect(second.result.current.fromSavedCopy).toBe(true);
  });
});
