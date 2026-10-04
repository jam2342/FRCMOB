import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { updatePicklist, type Picklist, type PicklistUpdateResult } from '../../api';
import { signInTestWorkspace } from '../../test/workspace';
import { storageMethodTarget } from '../../test/storage';
import { setWorkspaceSession } from '../workspace/workspaceSession';
import { usePicklistEditor } from './usePicklistEditor';

vi.mock('../../api', () => ({ updatePicklist: vi.fn(), QueuedForSyncError: class extends Error {} }));
let id = 100;
const list = (): Picklist => ({ id: ++id, event_key: '2026test', title: 'Saturday', version: 1,
  live_mode: false, archived: false, created_by: null, created_at: null, updated_at: null,
  slots: [{ team_key: 'frc254', notes: '', dnp_reason: '', tier: 'first', status: 'available', picked_by_alliance: null }] });
const note = (value: string) => (doc: Picklist): Picklist => ({ ...doc, slots: doc.slots.map(s => ({ ...s, notes: value })) });
const wait = () => new Promise<PicklistUpdateResult>(resolve => { release = resolve; });
let release: (value: PicklistUpdateResult) => void;

beforeEach(() => { vi.useFakeTimers(); localStorage.clear(); signInTestWorkspace(); vi.mocked(updatePicklist).mockReset(); });
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

describe('picklist draft recovery and shared saves', () => {
  it('serializes slow saves and submits edits made during an earlier save on the acknowledged version', async () => {
    const server = list();
    vi.mocked(updatePicklist).mockImplementationOnce(wait).mockImplementation(async (_, body) => ({ ok: true, picklist: { ...server, ...body, version: 3 } }));
    const { result } = renderHook(() => usePicklistEditor(1));
    act(() => { result.current.select(server); result.current.edit(note('first')); });
    await act(() => vi.advanceTimersByTimeAsync(900));
    act(() => result.current.edit(note('newer')));
    await act(() => vi.advanceTimersByTimeAsync(1000));
    expect(updatePicklist).toHaveBeenCalledTimes(1);
    await act(async () => release({ ok: true, picklist: { ...note('first')(server), version: 2 } }));
    expect(updatePicklist).toHaveBeenCalledTimes(2);
    expect(vi.mocked(updatePicklist).mock.calls[1][1]).toMatchObject({ version: 2, slots: [{ notes: 'newer' }] });
    expect(result.current.doc?.slots[0].notes).toBe('newer');
    expect(result.current.pending).toBe(false);
  });

  it('keeps a pending edit when switching lists and updates only its original list', async () => {
    const first = list(), second = list();
    vi.mocked(updatePicklist).mockImplementation(async (_, body) => ({ ok: true, picklist: { ...first, ...body, version: 2 } }));
    const { result } = renderHook(() => usePicklistEditor(1));
    act(() => { result.current.select(first); result.current.edit(note('keep first')); result.current.select(second); });
    await act(() => vi.advanceTimersByTimeAsync(900));
    expect(updatePicklist).toHaveBeenCalledWith(first.id, expect.objectContaining({ slots: [expect.objectContaining({ notes: 'keep first' })] }));
    expect(result.current.doc?.id).toBe(second.id);
    act(() => result.current.select(first));
    expect(result.current.doc?.slots[0].notes).toBe('keep first');
  });

  it('recovers an unsaved draft after unmount and keeps it out of another workspace', () => {
    const server = list();
    const first = renderHook(() => usePicklistEditor(1));
    act(() => { first.result.current.select(server); first.result.current.edit(note('recover')); });
    expect(JSON.parse(localStorage.getItem(`frcmob_picklist_draft_v1:1:${server.id}`)!).doc.slots[0].notes).toBe('recover');
    first.unmount();
    const restored = renderHook(() => usePicklistEditor(1));
    act(() => restored.result.current.select(server));
    expect(restored.result.current.doc?.slots[0].notes).toBe('recover');
    const other = renderHook(() => usePicklistEditor(2));
    act(() => other.result.current.select(server));
    expect(other.result.current.doc?.slots[0].notes).toBe('');
  });

  it('keeps failed saves and retries the current edit', async () => {
    const server = list();
    vi.mocked(updatePicklist).mockRejectedValueOnce(new Error('Fixture disconnected')).mockImplementation(async (_, body) => ({ ok: true, picklist: { ...server, ...body, version: 2 } }));
    const { result } = renderHook(() => usePicklistEditor(1));
    act(() => { result.current.select(server); result.current.edit(note('retained')); });
    await act(() => vi.advanceTimersByTimeAsync(900));
    expect(result.current.pending).toBe(true);
    expect(result.current.notice).toContain('Fixture disconnected');
    await act(async () => result.current.retry());
    expect(result.current.pending).toBe(false);
    expect(result.current.doc?.slots[0].notes).toBe('retained');
  });

  it('preserves conflict edits, pauses automatic writes, and requires an explicit choice', async () => {
    const server = list(), shared = { ...note('another scout')(server), version: 2 };
    vi.mocked(updatePicklist).mockResolvedValueOnce({ ok: false, conflict: true, picklist: shared }).mockImplementation(async (_, body) => ({ ok: true, picklist: { ...server, ...body, version: 3 } }));
    const { result } = renderHook(() => usePicklistEditor(1));
    act(() => { result.current.select(server); result.current.edit(note('my edit')); });
    await act(() => vi.advanceTimersByTimeAsync(900));
    expect(result.current.conflict).toBe(true);
    expect(result.current.doc?.slots[0].notes).toBe('my edit');
    act(() => result.current.edit(note('newer local edit')));
    await act(() => vi.advanceTimersByTimeAsync(900));
    expect(updatePicklist).toHaveBeenCalledTimes(1);
    await act(async () => result.current.retry());
    expect(vi.mocked(updatePicklist).mock.calls[1][1]).toMatchObject({ version: 2, slots: [{ notes: 'newer local edit' }] });
    expect(result.current.conflict).toBe(false);
  });

  it('allows discarding a local conflict in favor of the shared version', () => {
    const server = list();
    const { result } = renderHook(() => usePicklistEditor(1));
    act(() => { result.current.select(server); result.current.edit(note('my edit')); result.current.select({ ...note('shared')(server), version: 2 }); });
    act(() => result.current.discard());
    expect(result.current.doc?.slots[0].notes).toBe('shared');
    expect(result.current.pending).toBe(false);
  });

  it('does not issue delayed saves under a new workspace', async () => {
    const server = list();
    const { result } = renderHook(() => usePicklistEditor(1));
    act(() => { result.current.select(server); result.current.edit(note('private')); });
    setWorkspaceSession({ token: 'second-fixture', expiresAt: Date.now() / 1000 + 3600, workspace: { id: 2, name: 'Other', frc_team_number: 118 }, me: { id: 2, display_name: 'Other', role: 'member' } });
    await act(() => vi.advanceTimersByTimeAsync(900));
    expect(updatePicklist).not.toHaveBeenCalled();
    expect(result.current.pending).toBe(true);
  });

  it('shows a storage warning and retains memory-only edits through route changes', () => {
    const server = list();
    const original = window.localStorage.setItem.bind(window.localStorage);
    vi.spyOn(storageMethodTarget(), 'setItem').mockImplementation((key, value) => {
      if (key.startsWith('frcmob_picklist_draft')) throw new DOMException('Full', 'QuotaExceededError');
      return original(key, value);
    });
    const first = renderHook(() => usePicklistEditor(1));
    act(() => { first.result.current.select(server); first.result.current.edit(note('memory only')); });
    expect(first.result.current.notice).toContain('storage is full');
    first.unmount();
    const restored = renderHook(() => usePicklistEditor(1));
    act(() => restored.result.current.select(server));
    expect(restored.result.current.doc?.slots[0].notes).toBe('memory only');
  });
});
