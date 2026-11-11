import { act, cleanup, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { PitAssignment } from '../../api';

const online = vi.hoisted(() => ({ value: true }));
vi.mock('../../hooks/useOnlineStatus', () => ({ useOnlineStatus: () => ({ online: online.value }) }));
vi.mock('../../api', () => ({
  getPitAssignments: vi.fn(),
  claimPitTeam: vi.fn(),
  releasePitTeam: vi.fn(),
  setPitAssignment: vi.fn(),
  autoSplitPitTeams: vi.fn(),
}));
import { autoSplitPitTeams, claimPitTeam, getPitAssignments, releasePitTeam, setPitAssignment } from '../../api';
import { usePitAssignments } from './usePitAssignments';

const row = (teamKey: string, id: number, name: string): PitAssignment => ({
  team_key: teamKey, member_id: id, member_name: name, source: 'claim', assigned_by_member_id: id, updated_at: null,
});
const me = { id: 7, display_name: 'Jamal' };

function setHidden(hidden: boolean) {
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => (hidden ? 'hidden' : 'visible') });
  document.dispatchEvent(new Event('visibilitychange'));
}

async function mount() {
  const hook = renderHook(() => usePitAssignments({ eventKey: '2026arc', me, enabled: true }));
  await act(async () => {});
  return hook;
}

beforeEach(() => {
  online.value = true;
  setHidden(false);
  vi.mocked(getPitAssignments).mockResolvedValue([row('frc254', 8, 'Alex')]);
});
afterEach(() => { cleanup(); vi.clearAllMocks(); vi.useRealTimers(); });

it('loads assignments with the page and keys them by lowercase team', async () => {
  const { result } = await mount();
  expect(getPitAssignments).toHaveBeenCalledWith('2026arc');
  expect(result.current.ready).toBe(true);
  expect(result.current.byTeam.get('frc254')?.member_name).toBe('Alex');
});

it('shows a claim as mine straight away, then keeps the server answer', async () => {
  let finish: (value: { ok: boolean; assignment: PitAssignment }) => void = () => {};
  vi.mocked(claimPitTeam).mockReturnValue(new Promise((resolve) => { finish = resolve; }));
  const { result } = await mount();
  let done: Promise<boolean> = Promise.resolve(false);
  act(() => { done = result.current.claim('frc111'); });
  expect(result.current.byTeam.get('frc111')?.member_id).toBe(7);
  expect(result.current.pending.has('frc111')).toBe(true);
  vi.mocked(getPitAssignments).mockResolvedValue([row('frc254', 8, 'Alex'), row('frc111', 7, 'Jamal')]);
  await act(async () => { finish({ ok: true, assignment: row('frc111', 7, 'Jamal') }); await done; });
  expect(result.current.byTeam.get('frc111')?.member_name).toBe('Jamal');
  expect(result.current.pending.size).toBe(0);
  expect(result.current.notice).toBeNull();
});

it('rolls a 409 back, shows the server sentence and re-reads the assignments', async () => {
  vi.mocked(claimPitTeam).mockRejectedValue(new Error('frc254 is already taken by Alex.'));
  vi.mocked(getPitAssignments).mockResolvedValue([]);
  const { result } = await mount();
  expect(result.current.byTeam.has('frc254')).toBe(false);
  const calls = vi.mocked(getPitAssignments).mock.calls.length;
  await act(async () => { await result.current.claim('frc254'); });
  expect(result.current.notice?.text).toBe('frc254 is already taken by Alex.');
  expect(result.current.byTeam.has('frc254')).toBe(false);
  expect(vi.mocked(getPitAssignments).mock.calls.length).toBeGreaterThan(calls);
});

it('restores the other scout when a claim over their team fails', async () => {
  vi.mocked(claimPitTeam).mockRejectedValue(new Error('frc254 is already taken by Alex.'));
  const { result } = await mount();
  await act(async () => { await result.current.claim('frc254'); });
  expect(result.current.byTeam.get('frc254')?.member_name).toBe('Alex');
});

it('releases right away and brings the team back if the server refuses', async () => {
  vi.mocked(getPitAssignments).mockResolvedValue([row('frc111', 7, 'Jamal')]);
  vi.mocked(releasePitTeam).mockResolvedValueOnce({ ok: true, released: true });
  const { result } = await mount();
  vi.mocked(getPitAssignments).mockResolvedValue([]);
  await act(async () => { await result.current.release('frc111'); });
  expect(releasePitTeam).toHaveBeenCalledWith('2026arc', 'frc111');
  expect(result.current.byTeam.has('frc111')).toBe(false);

  vi.mocked(releasePitTeam).mockRejectedValueOnce(new Error('Only the scout who took this team can release it.'));
  vi.mocked(getPitAssignments).mockResolvedValue([row('frc111', 7, 'Jamal')]);
  await act(async () => { await result.current.release('frc111'); });
  expect(result.current.byTeam.get('frc111')?.member_id).toBe(7);
  expect(result.current.notice?.tone).toBe('warn');
});

it('passes force through and reassigns with the member id', async () => {
  vi.mocked(claimPitTeam).mockResolvedValue({ ok: true, assignment: row('frc254', 7, 'Jamal') });
  vi.mocked(setPitAssignment).mockResolvedValue({ ok: true, assignment: row('frc254', 9, 'Sam') });
  const { result } = await mount();
  await act(async () => { await result.current.claim('frc254', { force: true }); });
  expect(claimPitTeam).toHaveBeenCalledWith('2026arc', 'frc254', { force: true });
  await act(async () => { await result.current.reassign('frc254', { id: 9, name: 'Sam' }); });
  expect(setPitAssignment).toHaveBeenCalledWith('2026arc', 'frc254', 9);
  await act(async () => { await result.current.reassign('frc254', null); });
  expect(setPitAssignment).toHaveBeenLastCalledWith('2026arc', 'frc254', null);
});

it('auto-split asks only for unscouted teams and returns the new state', async () => {
  const split = [row('frc111', 8, 'Alex'), row('frc222', 9, 'Sam')];
  vi.mocked(autoSplitPitTeams).mockResolvedValue(split);
  vi.mocked(getPitAssignments).mockResolvedValue(split);
  const { result } = await mount();
  let rows: PitAssignment[] | null = null;
  await act(async () => { rows = await result.current.autoSplit(['frc111', 'frc222'], [8, 9]); });
  expect(autoSplitPitTeams).toHaveBeenCalledWith('2026arc', { teamKeys: ['frc111', 'frc222'], memberIds: [8, 9], onlyUnscouted: true });
  expect(rows).toHaveLength(2);
  expect(result.current.byTeam.get('frc222')?.member_name).toBe('Sam');
});

it('offline: keeps the last assignments, refuses to claim or release, and sends nothing', async () => {
  const { result, rerender } = await mount();
  online.value = false;
  rerender();
  await act(async () => {
    expect(await result.current.claim('frc111')).toBe(false);
    expect(await result.current.release('frc254')).toBe(false);
  });
  expect(claimPitTeam).not.toHaveBeenCalled();
  expect(releasePitTeam).not.toHaveBeenCalled();
  expect(result.current.byTeam.get('frc254')?.member_name).toBe('Alex');
  expect(result.current.notice?.text).toMatch(/offline/i);
});

it('polls every 20 seconds while visible and stops while the page is hidden', async () => {
  vi.useFakeTimers();
  renderHook(() => usePitAssignments({ eventKey: '2026arc', me, enabled: true }));
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  expect(getPitAssignments).toHaveBeenCalledTimes(1);
  await act(async () => { await vi.advanceTimersByTimeAsync(20_000); });
  expect(getPitAssignments).toHaveBeenCalledTimes(2);

  act(() => setHidden(true));
  await act(async () => { await vi.advanceTimersByTimeAsync(90_000); });
  expect(getPitAssignments).toHaveBeenCalledTimes(2);

  act(() => setHidden(false));
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  expect(getPitAssignments).toHaveBeenCalledTimes(3);
});

it('a poll that started before a claim does not put the old state back', async () => {
  let answerPoll: (rows: PitAssignment[]) => void = () => {};
  vi.mocked(claimPitTeam).mockResolvedValue({ ok: true, assignment: row('frc111', 7, 'Jamal') });
  const { result } = await mount();
  vi.mocked(getPitAssignments).mockReturnValueOnce(new Promise((resolve) => { answerPoll = resolve; }));
  act(() => result.current.refresh());
  await act(async () => { await result.current.claim('frc111'); });
  vi.mocked(getPitAssignments).mockResolvedValue([row('frc254', 8, 'Alex'), row('frc111', 7, 'Jamal')]);
  await act(async () => { answerPoll([row('frc254', 8, 'Alex')]); });
  expect(result.current.byTeam.get('frc111')?.member_id).toBe(7);
});
