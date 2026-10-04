import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { IDBFactory } from 'fake-indexeddb';
const vault = vi.hoisted(() => ({ get: vi.fn(), set: vi.fn(), remove: vi.fn(), encrypt: vi.fn(), decrypt: vi.fn() }));
vi.mock('./runtime', () => ({ isNativeApp: () => true }));
vi.mock('./secureStorage', () => ({ SecureStorage: vault }));
const key = 'frcmob_workspace_session_v1';
const session = { token: 'fixture-secret', expiresAt: Date.now() / 1000 + 3600,
  workspace: { id: 42, name: 'Fixture', frc_team_number: null }, me: { id: 1, display_name: 'Scout', role: 'member' as const } };
let saved: string | null;
beforeEach(() => {
  vi.resetModules(); vi.clearAllMocks(); window.localStorage.clear(); saved = null;
  globalThis.indexedDB = new IDBFactory();
  vault.get.mockImplementation(async () => ({ value: saved }));
  vault.set.mockImplementation(async ({ value }: { value: string }) => { saved = value; });
  vault.remove.mockImplementation(async () => { saved = null; });
  vault.encrypt.mockImplementation(async ({ value }: { value: string }) => ({ value: btoa(value) }));
  vault.decrypt.mockImplementation(async ({ value }: { value: string }) => ({ value: atob(value) }));
});
afterEach(() => vi.unstubAllGlobals());
describe('native workspace credentials', () => {
  it('migrates without plaintext and restores access after restart', async () => {
    window.localStorage.setItem(key, JSON.stringify(session));
    let store = await import('../features/workspace/workspaceSession');
    expect(store.getWorkspaceSession()).toBeNull(); await store.bootstrapWorkspaceSession();
    expect(saved).toBe(JSON.stringify(session)); expect(window.localStorage.getItem(key)).toBeNull();
    vi.resetModules(); store = await import('../features/workspace/workspaceSession');
    await store.bootstrapWorkspaceSession(); expect(store.getWorkspaceToken()).toBe(session.token);
  });
  it('preserves the original when secure migration fails', async () => {
    window.localStorage.setItem(key, JSON.stringify(session)); vault.set.mockRejectedValueOnce(new Error('locked'));
    const store = await import('../features/workspace/workspaceSession');
    await expect(store.bootstrapWorkspaceSession()).rejects.toThrow('locked');
    expect(window.localStorage.getItem(key)).toContain(session.token); expect(store.getWorkspaceSession()).toBeNull();
  });
  it('refuses a new sign-in when persistence fails', async () => {
    const store = await import('../features/workspace/workspaceSession'); vault.set.mockRejectedValueOnce(new Error('full'));
    await expect(store.setWorkspaceSession(session)).rejects.toThrow('full');
    expect(store.getWorkspaceSession()).toBeNull(); expect(window.localStorage.getItem(key)).toBeNull();
  });
  it('does not restore access when sign-out races a slow sign-in', async () => {
    const store = await import('../features/workspace/workspaceSession'); let release!: () => void;
    vault.set.mockImplementationOnce(() => new Promise<void>(resolve => { release = () => { saved = JSON.stringify(session); resolve(); }; }));
    const write = store.setWorkspaceSession(session); await Promise.resolve();
    store.clearWorkspaceSession('left'); release(); await write;
    await vi.waitFor(() => expect(vault.remove).toHaveBeenCalled());
    expect(store.getWorkspaceSession()).toBeNull(); expect(saved).toBeNull();
  });
  it('retries a failed sign-out on next startup', async () => {
    saved = JSON.stringify(session); const store = await import('../features/workspace/workspaceSession'); await store.bootstrapWorkspaceSession();
    vault.remove.mockRejectedValueOnce(new Error('locked')); store.clearWorkspaceSession('left');
    await vi.waitFor(() => expect(vault.remove).toHaveBeenCalled()); vi.resetModules();
    const next = await import('../features/workspace/workspaceSession'); await next.bootstrapWorkspaceSession();
    expect(next.getWorkspaceSession()).toBeNull(); expect(saved).toBeNull();
  });
});
async function storedRows() {
  const db = await new Promise<IDBDatabase>((resolve, reject) => {
    const r = indexedDB.open('frcmob_offline_queue'); r.onsuccess = () => resolve(r.result); r.onerror = () => reject(r.error);
  });
  try { return await new Promise<Record<string, unknown>[]>((resolve, reject) => {
    const r = db.transaction('mutations').objectStore('mutations').getAll(); r.onsuccess = () => resolve(r.result); r.onerror = () => reject(r.error);
  }); } finally { db.close(); }
}
describe('native queued credentials', () => {
  it('protects legacy edits and decrypts only for replay', async () => {
    const item = { id: 'legacy', queuedAt: '2026-01-01', url: '/api/fixture', method: 'POST', body: '{"fuel":42}', headers: { 'X-Workspace-Access': session.token }, attempts: 0 };
    window.localStorage.setItem('frcmob_offline_queue_v1', JSON.stringify([item]));
    const queue = await import('../utils/offlineQueue'); await queue.bootstrapNativeQueue();
    const rows = await storedRows(); expect(rows[0].secureVersion).toBe(1); expect(JSON.stringify(rows)).not.toContain(session.token);
    expect(window.localStorage.getItem('frcmob_offline_queue_v1')).toBeNull();
    const fetcher = vi.fn().mockResolvedValue(new Response('{"ok":true}')); vi.stubGlobal('fetch', fetcher);
    await expect(queue.flush()).resolves.toBe(1); expect(fetcher.mock.calls[0][1].headers['X-Workspace-Access']).toBe(session.token);
    expect(await storedRows()).toHaveLength(0);
  });
  it('keeps originals and refuses plaintext fallback when encryption fails', async () => {
    window.localStorage.setItem('frcmob_offline_queue_v1', JSON.stringify([{ id: 'legacy', headers: { 'X-Workspace-Access': session.token } }]));
    vault.encrypt.mockRejectedValueOnce(new Error('locked')); const queue = await import('../utils/offlineQueue');
    await expect(queue.bootstrapNativeQueue()).rejects.toThrow('locked');
    expect(window.localStorage.getItem('frcmob_offline_queue_v1')).toContain(session.token); expect(await storedRows()).toHaveLength(0);
  });
});
