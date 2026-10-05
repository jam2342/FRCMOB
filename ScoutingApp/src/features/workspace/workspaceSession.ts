import { isNativeApp } from '../../platform/runtime';
import { SecureStorage } from '../../platform/secureStorage';
// Browser sessions persist in localStorage; native sessions use the OS vault.
// Startup awaits native migration before the UI or any sync starts. A server 401
// still revokes access immediately.

export const WORKSPACE_ACCESS_HEADER = 'X-Workspace-Access';
export const WORKSPACE_ACCESS_QUERY_PARAM = 'workspace_access';
const STORAGE_KEY = 'frcmob_workspace_session_v1';
// Kept beside the session so a reload, or another tab that noticed the removal
// first, still explains why the device is signed out.
const CLEAR_PENDING_KEY = 'frcmob_workspace_clear_pending_v1';
const END_REASON_KEY = 'frcmob_workspace_end_reason_v1';

export type WorkspaceRole = 'leader' | 'member';

export interface WorkspaceSummary {
  id: number;
  name: string;
  frc_team_number: number | null;
}

export interface WorkspaceMe {
  id: number;
  display_name: string;
  role: WorkspaceRole;
}

export interface WorkspaceSession {
  token: string;
  expiresAt: number;
  workspace: WorkspaceSummary;
  me: WorkspaceMe;
}

// Why the session ended, so the Team page can say so instead of just showing
// the join form again.
export type WorkspaceEndReason = 'left' | 'revoked' | 'expired';

type Listener = () => void;
const listeners = new Set<Listener>();
let revision = 0;
let cached: WorkspaceSession | null | undefined;
let lastEndReason: WorkspaceEndReason | null = null;
// A join code is only returned when issued. Creating a workspace switches the
// Team page to the workspace view before the create call returns, so the code
// waits here (memory only, never storage) for that view to show it.
let issuedJoinCode: { workspaceId: number; code: string } | null = null;

function storage(): Storage | null {
  try {
    return typeof window === 'undefined' ? null : window.localStorage;
  } catch {
    return null;
  }
}

function parse(raw: string | null): WorkspaceSession | null {
  if (!raw) return null;
  try {
    const value = JSON.parse(raw) as Partial<WorkspaceSession>;
    if (
      typeof value.token !== 'string' ||
      !value.token ||
      typeof value.expiresAt !== 'number' ||
      typeof value.workspace?.id !== 'number' ||
      typeof value.me?.id !== 'number'
    ) {
      return null;
    }
    return value as WorkspaceSession;
  } catch {
    return null;
  }
}

function emit() {
  listeners.forEach((listener) => listener());
}

export function getWorkspaceSession(): WorkspaceSession | null {
  if (cached === undefined) cached = isNativeApp() ? null : parse(storage()?.getItem(STORAGE_KEY) ?? null);
  if (cached && cached.expiresAt * 1000 <= Date.now()) {
    clearWorkspaceSession('expired');
  }
  return cached ?? null;
}

export function getWorkspaceToken(): string {
  return getWorkspaceSession()?.token ?? '';
}

export function setWorkspaceSession(session: WorkspaceSession): void | Promise<void> {
  const currentRevision = ++revision;
  const publish = () => {
    if (currentRevision !== revision) return;
    cached = session;
    lastEndReason = null;
    try {
      storage()?.removeItem(END_REASON_KEY);
      if (!isNativeApp()) storage()?.setItem(STORAGE_KEY, JSON.stringify(session));
    } catch { /* Browser private mode: keep the current tab signed in. */ }
    emit();
  };
  if (!isNativeApp()) return publish();
  return persistNativeSession(JSON.stringify(session)).then(() => {
    if (currentRevision !== revision) return;
    storage()?.removeItem(CLEAR_PENDING_KEY);
    storage()?.removeItem(STORAGE_KEY);
    publish();
  });
}

// Refresh the names/role shown in the UI without touching the token.
export function updateWorkspaceSessionDetails(workspace: WorkspaceSummary, me: WorkspaceMe) {
  const current = getWorkspaceSession();
  if (!current || current.workspace.id !== workspace.id) return;
  void Promise.resolve(setWorkspaceSession({ ...current, workspace, me })).catch(reportSecureStorageFailure);
}

export function rememberIssuedJoinCode(workspaceId: number, code: string) {
  issuedJoinCode = { workspaceId, code };
}

export function issuedJoinCodeFor(workspaceId: number): string | null {
  return issuedJoinCode?.workspaceId === workspaceId ? issuedJoinCode.code : null;
}

export function clearWorkspaceSession(reason: WorkspaceEndReason) {
  issuedJoinCode = null;
  const clearRevision = ++revision;
  if (!isNativeApp() && cached === null && storage()?.getItem(STORAGE_KEY) === null) return;
  cached = null;
  // Pit photos cached for offline belong to the team being left.
  if (typeof caches !== 'undefined') void caches.delete('frcmob-pit-photos-v1').catch(() => {});
  if (isNativeApp()) {
    try { storage()?.setItem(CLEAR_PENDING_KEY, '1'); } catch { reportSecureStorageFailure(); }
    void persistNativeSession(null).then(() => { if (clearRevision === revision) storage()?.removeItem(CLEAR_PENDING_KEY); }).catch(reportSecureStorageFailure);
  }
  lastEndReason = reason;
  try {
    storage()?.setItem(END_REASON_KEY, reason);
    storage()?.removeItem(STORAGE_KEY);
  } catch {
    // Nothing stored to remove.
  }
  emit();
}

export function workspaceEndReason(): WorkspaceEndReason | null {
  if (lastEndReason || getWorkspaceSession()) return lastEndReason;
  const stored = storage()?.getItem(END_REASON_KEY);
  return stored === 'left' || stored === 'revoked' || stored === 'expired' ? stored : null;
}

export function subscribeWorkspaceSession(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

// Another tab joining or leaving changes localStorage; follow it here too.
if (typeof window !== 'undefined') {
  window.addEventListener('storage', (event) => {
    if (isNativeApp() || event.key !== STORAGE_KEY) return;
    cached = parse(event.newValue);
    emit();
  });
}

// Serialize updates and removals so a slow save cannot restore a signed-out session.
let nativeWrites: Promise<void> = Promise.resolve();
function persistNativeSession(value: string | null): Promise<void> {
  const write = nativeWrites.then(() => value === null
    ? SecureStorage.remove({ key: STORAGE_KEY })
    : SecureStorage.set({ key: STORAGE_KEY, value }));
  nativeWrites = write.catch(() => {});
  return write;
}
function reportSecureStorageFailure() {
  window.dispatchEvent(new Event('frcmob:secure-storage-error'));
}

export async function bootstrapWorkspaceSession(): Promise<void> {
  if (!isNativeApp()) return;
  if (storage()?.getItem(CLEAR_PENDING_KEY)) {
    await persistNativeSession(null);
    storage()?.removeItem(STORAGE_KEY);
    storage()?.removeItem(CLEAR_PENDING_KEY);
  }
  const protectedValue = (await SecureStorage.get({ key: STORAGE_KEY })).value;
  const legacy = storage()?.getItem(STORAGE_KEY) ?? null;
  const session = parse(protectedValue ?? legacy);
  if (session && session.expiresAt * 1000 > Date.now()) {
    if (!protectedValue) await persistNativeSession(JSON.stringify(session));
    cached = session;
  } else {
    await persistNativeSession(null);
    cached = null;
    if (session) lastEndReason = 'expired';
  }
  // Remove plaintext only after secure persistence succeeds.
  storage()?.removeItem(STORAGE_KEY);
}
