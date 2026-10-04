// Signs the test "device" in to a team workspace, for tests of team-only features.
import { clearWorkspaceSession, setWorkspaceSession, type WorkspaceRole } from '../features/workspace/workspaceSession';

export function signInTestWorkspace(role: WorkspaceRole = 'leader') {
  setWorkspaceSession({
    token: 'test-workspace-token',
    expiresAt: Math.floor(Date.now() / 1000) + 3600,
    workspace: { id: 1, name: 'Test Team', frc_team_number: 118 },
    me: { id: 1, display_name: 'Test Scout', role },
  });
}

export function signOutTestWorkspace() {
  clearWorkspaceSession('left');
}
