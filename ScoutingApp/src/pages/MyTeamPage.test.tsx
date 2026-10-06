import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createWorkspace, ensureTeamRoom, getEventSchedule, joinWorkspace, getMyWorkspace } from '../api';
import { MyTeamPage } from './MyTeamPage';
import { WorkspaceGate } from '../features/workspace/WorkspaceGate';
import {
  clearWorkspaceSession,
  rememberIssuedJoinCode,
  setWorkspaceSession,
} from '../features/workspace/workspaceSession';
import { signInTestWorkspace, signOutTestWorkspace } from '../test/workspace';

const members = [
  { id: 1, display_name: 'Test Scout', role: 'leader', joined_at: null, last_seen_at: new Date().toISOString() },
  { id: 2, display_name: 'Sam', role: 'member', joined_at: null, last_seen_at: null },
];

vi.mock('../api', () => ({
  createWorkspace: vi.fn(async () => {
    // Mirrors the real client: the session is set before the call returns.
    rememberIssuedJoinCode(1, 'ABCDE-12345');
    setWorkspaceSession({
      token: 'tok',
      expiresAt: Math.floor(Date.now() / 1000) + 3600,
      workspace: { id: 1, name: 'Robonauts', frc_team_number: 118 },
      me: { id: 1, display_name: 'Test Scout', role: 'leader' },
    });
    return {};
  }),
  joinWorkspace: vi.fn(async () => {
    throw new Error('No workspace uses that code. Check it with your team lead.');
  }),
  getMyWorkspace: vi.fn(async () => ({
    ok: true,
    workspace: { id: 1, name: 'Robonauts', frc_team_number: 118, join_code_rotated_at: null, created_at: null },
    me: members[0],
    members,
  })),
  removeWorkspaceMember: vi.fn(async () => ({
    ok: true,
    workspace: { id: 1, name: 'Robonauts', frc_team_number: 118, join_code_rotated_at: null, created_at: null },
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

const WEEK0_SCHEDULE = {
  ok: true,
  event_key: '2026week0',
  event_name: 'Week 0',
  matches: [1, 2].map((n) => ({
    match_key: `2026week0_qm${n}`,
    display_name: `QM ${n}`,
    comp_level: 'qm',
    set_number: 1,
    match_number: n,
    is_completed: false,
    red: [{ team_key: `frc${n}1` }, { team_key: `frc${n}2` }, { team_key: `frc${n}3` }],
    blue: [{ team_key: `frc${n}4` }, { team_key: `frc${n}5` }, { team_key: `frc${n}6` }],
  })),
};

function teamSnapshot(role: 'leader' | 'member') {
  const me = role === 'leader'
    ? { member_id: 1, display_name: 'Test Scout', role: 'leader' as const }
    : { member_id: 2, display_name: 'Sam', role: 'member' as const };
  return {
    ok: true,
    created: false,
    room_key: 'team-room-1',
    event_key: '2026week0',
    me,
    members: [
      { member_id: 1, display_name: 'Test Scout', role: 'leader' as const },
      { member_id: 2, display_name: 'Sam', role: 'member' as const },
    ],
    assignments: [{
      match_key: '2026week0_qm2',
      team_key: 'frc25',
      assigned_member_id: 2,
      assigned_display_name: 'Sam',
      member_active: true,
      covered: false,
      covered_by_me: false,
    }],
  };
}

function renderPage() {
  return render(
    <MemoryRouter>
      <MyTeamPage />
    </MemoryRouter>,
  );
}

describe('My Team page', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    signOutTestWorkspace();
  });
  afterEach(() => {
    clearWorkspaceSession('left');
  });

  it('offers join and create when the device has no team', async () => {
    renderPage();
    expect(screen.getByRole('heading', { name: 'Join your team' })).toBeTruthy();
    expect(screen.getByRole('heading', { name: 'Create a workspace' })).toBeTruthy();

    fireEvent.change(screen.getByLabelText(/Join code/), { target: { value: 'ABCDE12345' } });
    fireEvent.change(screen.getAllByLabelText(/Your name/)[0], { target: { value: 'Sam' } });
    fireEvent.click(screen.getByRole('button', { name: 'Join team' }));
    expect(await screen.findByText(/No workspace uses that code/)).toBeTruthy();
  });

  it('keeps malformed numbers and blank names out of workspace requests', async () => {
    renderPage();
    fireEvent.change(screen.getByLabelText(/Workspace name/),{target:{value:'Fixture'}});
    fireEvent.change(screen.getAllByLabelText(/Your name/)[1],{target:{value:'Scout'}});
    fireEvent.change(screen.getByLabelText(/FRC team number/),{target:{value:'-254'}});
    fireEvent.click(screen.getByRole('button',{name:'Create workspace'}));
    expect(await screen.findByRole('alert')).toHaveTextContent('digits only');expect(createWorkspace).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText(/Join code/),{target:{value:'ABCDE12345'}});
    fireEvent.change(screen.getAllByLabelText(/Your name/)[0],{target:{value:'   '}});
    fireEvent.click(screen.getByRole('button',{name:'Join team'}));expect(joinWorkspace).not.toHaveBeenCalled();
  });

  it('shows the new join code right after creating a workspace', async () => {
    renderPage();
    fireEvent.change(screen.getByLabelText(/Workspace name/), { target: { value: 'Robonauts' } });
    fireEvent.change(screen.getAllByLabelText(/Your name/)[1], { target: { value: 'Test Scout' } });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Create workspace' }));
    });
    expect(await screen.findByText('ABCDE-12345')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Copy code' })).toBeTruthy();
  });

  it('lets a leader remove a member and shows the replacement code', async () => {
    signInTestWorkspace('leader');
    renderPage();
    expect(await screen.findByText('Sam')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Remove' }));
    const dialog = await screen.findByRole('dialog');
    await act(async () => {
      fireEvent.click(within(dialog).getByRole('button', { name: 'Remove' }));
    });
    await waitFor(() => expect(screen.queryByText('Sam')).toBeNull());
    expect(screen.getByText('NEWCO-DE123')).toBeTruthy();
  });

  it('hides management controls from members', async () => {
    signInTestWorkspace('member');
    // The page trusts the server's view of the role, not the cached session.
    vi.mocked(getMyWorkspace).mockResolvedValueOnce({
      ok: true,
      workspace: { id: 1, name: 'Robonauts', frc_team_number: 118, join_code_rotated_at: null, created_at: null },
      me: { ...members[1], role: 'member' },
      members,
    } as never);
    renderPage();
    // Sam is both "You're Sam" and a row in the member list.
    expect((await screen.findAllByText('Sam')).length).toBe(2);
    expect(screen.queryByRole('button', { name: 'Remove' })).toBeNull();
    expect(screen.getByText(/Ask a team leader for the code/)).toBeTruthy();
  });

  it('deduplicates refresh triggers while a workspace request is in flight', async () => {
    signInTestWorkspace('leader');
    let releaseRequest: ((value: unknown) => void) | undefined;
    vi.mocked(getMyWorkspace).mockImplementationOnce(() => new Promise((resolve) => {
      releaseRequest = resolve;
    }) as never);

    renderPage();
    await waitFor(() => expect(getMyWorkspace).toHaveBeenCalledTimes(1));
    fireEvent.focus(window);
    fireEvent.focus(window);
    expect(getMyWorkspace).toHaveBeenCalledTimes(1);

    await act(async () => {
      releaseRequest?.({
        ok: true,
        workspace: { id: 1, name: 'Robonauts', frc_team_number: 118, join_code_rotated_at: null, created_at: null },
        me: members[0],
        members,
      });
    });
    fireEvent.focus(window);
    await waitFor(() => expect(getMyWorkspace).toHaveBeenCalledTimes(2));
  });
});

describe('My Team scouting card', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    localStorage.setItem('scouting_center_event_key', '2026week0');
    vi.mocked(getEventSchedule).mockResolvedValue(WEEK0_SCHEDULE as never);
  });
  afterEach(() => {
    clearWorkspaceSession('left');
  });

  it("shows a member their next match with a one-tap way to scout it", async () => {
    signInTestWorkspace('member', { id: 2, display_name: 'Sam' });
    vi.mocked(ensureTeamRoom).mockResolvedValue(teamSnapshot('member') as never);
    renderPage();

    expect(await screen.findByText('Scouting at Week 0')).toBeTruthy();
    expect(await screen.findByText('Your next match')).toBeTruthy();
    expect(screen.getByText('QM 2')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Scout team 25' }).getAttribute('href'))
      .toBe('/scouting?event=2026week0&match=2026week0_qm2&team=frc25');
    expect(screen.queryByRole('link', { name: 'Assign matches' })).toBeNull();
  });

  it('shows a leader coverage and the way into assignments', async () => {
    signInTestWorkspace('leader');
    vi.mocked(ensureTeamRoom).mockResolvedValue(teamSnapshot('leader') as never);
    renderPage();

    expect(await screen.findByText('1 / 12')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Assign matches' }).getAttribute('href'))
      .toBe('/scouting/assignments?event=2026week0');
  });
});

describe('WorkspaceGate', () => {
  afterEach(() => {
    clearWorkspaceSession('left');
  });

  it('explains and links to My Team instead of rendering team tools', () => {
    signOutTestWorkspace();
    render(
      <WorkspaceGate feature="Picklists">
        <p>secret picklist</p>
      </WorkspaceGate>,
    );
    expect(screen.queryByText('secret picklist')).toBeNull();
    expect(screen.getByText('Picklists are private to your team')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Set up your team' }).getAttribute('href')).toBe('#/my-team');
  });

  it('renders the tools for a team member', () => {
    signInTestWorkspace();
    render(
      <WorkspaceGate feature="Picklists">
        <p>secret picklist</p>
      </WorkspaceGate>,
    );
    expect(screen.getByText('secret picklist')).toBeTruthy();
  });
});
