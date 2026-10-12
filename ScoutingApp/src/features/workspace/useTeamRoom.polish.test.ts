import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
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


it('keeps an unavailable schedule separate from room success and retries it', async () => {
  api.getEventSchedule.mockRejectedValueOnce(new Error('offline'));
  signInAs(1, 'leader');
  api.ensureTeamRoom.mockResolvedValue(snapshot(1, 2));
  api.getTeamRoom.mockResolvedValue(snapshot(1, 2));
  const { result } = renderHook(() => useTeamRoom('2026week0'));
  await waitFor(() => expect(result.current.scheduleError).toBe('Could not load the match schedule.'));
  expect(result.current.schedule).toBeNull();
  expect(result.current.status).toBe('ready');
  await act(async () => window.dispatchEvent(new Event('focus')));
  await waitFor(() => expect(result.current.schedule).toEqual([]));
  expect(result.current.scheduleError).toBe('');
  api.getEventSchedule.mockRejectedValueOnce(new Error('offline again'));
  await act(async () => result.current.retry());
  await waitFor(() => expect(result.current.scheduleError).not.toBe(''));
  expect(result.current.schedule).toEqual([]);
  await act(async () => result.current.retry());
  await waitFor(() => expect(result.current.scheduleError).toBe(''));
});
