import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const state = vi.hoisted(() => ({ native: false, queued: [] as unknown[], pending: [] as unknown[], queueFails: false }));
vi.mock('./runtime', () => ({ isNativeApp: () => state.native }));
vi.mock('../utils/offlineQueue', () => ({
  listQueuedMutations: async () => { if (state.queueFails) throw new Error('idb'); return state.queued; },
}));
vi.mock('../features/onDevice/offlineStore', () => ({
  openDb: async () => ({ close: () => undefined }),
  listPendingSessions: async () => state.pending,
}));

import { moveToNewHostIfSafe, nothingLeftHere, OLD_HOST } from './hostMove';

const replace = vi.fn();
const at = (hostname: string) => ({ hostname, search: '?x=1', hash: '#/my-team', replace });

beforeEach(() => {
  Object.assign(state, { native: false, queued: [], pending: [], queueFails: false });
  localStorage.clear();
  replace.mockReset();
  vi.stubGlobal('indexedDB', {});
});
afterEach(() => vi.unstubAllGlobals());

describe('moving from the old address', () => {
  it('hands over to frcmob.com with the same route when nothing is waiting', async () => {
    expect(await moveToNewHostIfSafe(at(OLD_HOST))).toBe(true);
    expect(replace).toHaveBeenCalledWith('https://frcmob.com/?x=1#/my-team');
  });

  it.each([
    ['queued edits', () => { state.queued = [{ id: 'a' }]; }],
    ['recordings not yet uploaded', () => { state.pending = [{ id: 's' }]; }],
    ['an unsaved pit draft', () => { localStorage.setItem('frcmob_pit_draft_v1:1:2026txhou:frc118', '{}'); }],
    ['storage it cannot read', () => { state.queueFails = true; }],
  ])('stays while this browser holds %s', async (_label, arrange) => {
    arrange();
    expect(await nothingLeftHere()).toBe(false);
  });

  it('never moves frcmob.com itself, previews or the native app', async () => {
    expect(await moveToNewHostIfSafe(at('frcmob.com'))).toBe(false);
    expect(await moveToNewHostIfSafe(at('scouting-app-git-branch-jamals-projects.vercel.app'))).toBe(false);
    state.native = true;
    expect(await moveToNewHostIfSafe(at(OLD_HOST))).toBe(false);
    expect(replace).not.toHaveBeenCalled();
  });
});
