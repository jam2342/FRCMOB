import { useSyncExternalStore } from 'react';
import { getWorkspaceSession, subscribeWorkspaceSession, type WorkspaceSession } from './workspaceSession';

export function useWorkspace(): WorkspaceSession | null {
  return useSyncExternalStore(subscribeWorkspaceSession, getWorkspaceSession, () => null);
}
