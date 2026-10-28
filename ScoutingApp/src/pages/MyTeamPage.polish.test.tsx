import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import { MyTeamPage } from './MyTeamPage';
import {
  clearWorkspaceSession,
  rememberIssuedJoinCode,
  setWorkspaceSession,
} from '../features/workspace/workspaceSession';
import { signInTestWorkspace } from '../test/workspace';

const members = [
  { id: 1, display_name: 'Test Scout', role: 'leader', joined_at: null, last_seen_at: new Date().toISOString() },
  { id: 2, display_name: 'Sam', role: 'member', joined_at: null, last_seen_at: null },
];

vi.mock('../api', () => ({
  // The thank-you note has its own tests; here nobody has scouted yet.
  getScoutingCoverage: vi.fn(async () => ({ ok: true, event_key: '', summary: { total_slots: 0, covered_slots: 0, coverage_pct: 0, total_entries: 0, scout_count: 0, outlier_count: 0 }, grid: [], leaderboard: [], outliers: [] })),
  // The season card has its own tests; here the team hasn't played yet.
  getTeamSeasonStory: vi.fn(async () => ({ ok: true, team_key: '', nickname: null, season: 2026, game: null, events: [], totals: { wins: 0, losses: 0, ties: 0, events: 0, event_wins: 0, best_match: null } })),
  getWorkspaceSignupPolicy: vi.fn(async () => ({ required: false, site_key: null })),
  createWorkspace: vi.fn(async () => {
    // Mirrors the real client: the session is set before the call returns.
    rememberIssuedJoinCode(1, 'ABCDE-12345');
    setWorkspaceSession({
      token: 'tok',
      expiresAt: Math.floor(Date.now() / 1000) + 3600,
      workspace: { id: 1, name: 'Eagle Robotics', frc_team_number: 5417 },
      me: { id: 1, display_name: 'Test Scout', role: 'leader' },
    });
    return {};
  }),
  joinWorkspace: vi.fn(async () => {
    throw new Error('No workspace uses that code. Check it with your team lead.');
  }),
  getMyWorkspace: vi.fn(async () => ({
    ok: true,
    workspace: { id: 1, name: 'Eagle Robotics', frc_team_number: 5417, join_code_rotated_at: null, created_at: null },
    me: members[0],
    members,
  })),
  removeWorkspaceMember: vi.fn(async () => ({
    ok: true,
    workspace: { id: 1, name: 'Eagle Robotics', frc_team_number: 5417, join_code_rotated_at: null, created_at: null },
    me: members[0],
    members: [members[0]],
    join_code: 'NEWCO-DE123',
  })),
  leaveWorkspace: vi.fn(),
  renameMeInWorkspace: vi.fn(),
  rotateWorkspaceJoinCode: vi.fn(),
  setWorkspaceMemberRole: vi.fn(),
  updateMyWorkspace: vi.fn(),
  ensureTeamRoom: vi.fn(),
  getTeamRoom: vi.fn(async () => null),
  getEventSchedule: vi.fn(),
}));

vi.mock('../hooks/useOnlineStatus', () => ({
  useOnlineStatus: () => ({ online: true, queueSize: 0, isShowingOfflineData: false }),
}));

function renderPage() {
  return render(
    <MemoryRouter>
      <MyTeamPage />
    </MemoryRouter>,
  );
}


afterEach(() => { clearWorkspaceSession('left'); vi.restoreAllMocks(); });
it('keeps removal errors inside the dialog across background refreshes', async () => {
  localStorage.clear();
  signInTestWorkspace('leader');
  const { removeWorkspaceMember } = await import('../api');
  vi.mocked(removeWorkspaceMember).mockRejectedValueOnce(new Error('Could not remove Sam. Try again.'));
  renderPage();
  await screen.findByText('Sam');
  fireEvent.click(screen.getByRole('button', { name: 'Remove' }));
  const dialog = await screen.findByRole('dialog');
  fireEvent.click(within(dialog).getByRole('button', { name: 'Remove' }));
  expect(await within(dialog).findByRole('alert')).toHaveTextContent('Could not remove Sam. Try again.');
  await act(async () => window.dispatchEvent(new Event('focus')));
  expect(within(dialog).getByRole('alert')).toHaveTextContent('Could not remove Sam. Try again.');
});
