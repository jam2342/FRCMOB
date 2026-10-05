import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { TeamRoomSnapshot } from '../api';
import { ScoutingAssignPage } from './ScoutingAssignPage';
import { signInTestWorkspace, signOutTestWorkspace } from '../test/workspace';

const api = vi.hoisted(() => ({
  ensureTeamRoom: vi.fn(),
  getTeamRoom: vi.fn(),
  saveTeamRoomAssignments: vi.fn(),
  getEventSchedule: vi.fn(),
}));

vi.mock('../api', () => api);
const network = vi.hoisted(() => ({ online: true }));
vi.mock('../hooks/useOnlineStatus', () => ({
  useOnlineStatus: () => ({ online: network.online, queueSize: 0, isShowingOfflineData: false }),
}));

const MEMBERS = [
  { member_id: 1, display_name: 'Test Scout', role: 'leader' as const },
  { member_id: 2, display_name: 'Sam', role: 'member' as const },
];

function snapshot(overrides: Partial<TeamRoomSnapshot> = {}): TeamRoomSnapshot {
  return {
    ok: true,
    room_key: 'team-room-1',
    event_key: '2026week0',
    me: MEMBERS[0],
    members: MEMBERS,
    assignments: [],
    ...overrides,
  };
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/scouting/assignments?event=2026week0']}>
      <Routes>
        <Route path="/scouting/assignments" element={<ScoutingAssignPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  localStorage.clear();
  network.online = true;
  api.getEventSchedule.mockResolvedValue({
    ok: true,
    event_key: '2026week0',
    event_name: 'Week 0',
    count: 1,
    matches: [
      {
        match_key: '2026week0_qm1',
        display_name: 'QM 1',
        comp_level: 'qm',
        set_number: 1,
        match_number: 1,
        is_completed: false,
        red: [{ team_key: 'frc118' }, { team_key: 'frc148' }, { team_key: 'frc3310' }],
        blue: [{ team_key: 'frc254' }, { team_key: 'frc1114' }, { team_key: 'frc1678' }],
      },
    ],
  });
  api.getTeamRoom.mockResolvedValue(null);
});

afterEach(() => {
  signOutTestWorkspace();
  vi.clearAllMocks();
});

describe('Scouting assignments', () => {
  it('blocks editing offline and says why', async () => {
    network.online = false;
    signInTestWorkspace('leader');
    api.ensureTeamRoom.mockResolvedValue({ ...snapshot(), created: false });
    renderPage();

    expect(await screen.findByText(/You're offline/)).toBeInTheDocument();
    expect(screen.getByLabelText('Scout for team 254 in QM 1')).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Auto-assign' })).toBeDisabled();
  });

  it('shows a leader every workspace member without typing names', async () => {
    signInTestWorkspace('leader');
    api.ensureTeamRoom.mockResolvedValue({ ...snapshot(), created: true });
    renderPage();

    expect(await screen.findByText('Scouts (2)')).toBeInTheDocument();
    expect(screen.getAllByText(/Sam/).length).toBeGreaterThan(0);
    expect(screen.getByText('Live Scouting')).toBeInTheDocument();
    expect(api.ensureTeamRoom).toHaveBeenCalledWith('2026week0', expect.any(String));
  });

  it('saves a slot by member id and says so plainly when it fails', async () => {
    signInTestWorkspace('leader');
    api.ensureTeamRoom.mockResolvedValue({ ...snapshot(), created: false });
    api.saveTeamRoomAssignments.mockRejectedValueOnce(new Error('Server said no'));
    renderPage();

    const select = await screen.findByLabelText('Scout for team 254 in QM 1');
    fireEvent.change(select, { target: { value: '2' } });

    await waitFor(() => expect(api.saveTeamRoomAssignments).toHaveBeenCalledWith('2026week0', [
      { match_key: '2026week0_qm1', team_key: 'frc254', assigned_member_id: 2 },
    ]));
    expect(await screen.findByText(/Not saved/)).toBeInTheDocument();
    // The cell shows what the server has, not the failed choice.
    expect((select as HTMLSelectElement).value).toBe('');

    api.saveTeamRoomAssignments.mockResolvedValueOnce(snapshot({
      assignments: [{
        match_key: '2026week0_qm1',
        team_key: 'frc254',
        assigned_member_id: 2,
        assigned_display_name: 'Sam',
        member_active: true,
        covered: false,
        covered_by_me: false,
      }],
    }));
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    await waitFor(() => expect((select as HTMLSelectElement).value).toBe('2'));
    expect(screen.queryByText(/Not saved/)).not.toBeInTheDocument();
  });

  it('stops a scout from being picked for two teams in one match', async () => {
    signInTestWorkspace('leader');
    api.ensureTeamRoom.mockResolvedValue({
      ...snapshot({
        assignments: [{
          match_key: '2026week0_qm1',
          team_key: 'frc118',
          assigned_member_id: 2,
          assigned_display_name: 'Sam',
          member_active: true,
          covered: false,
          covered_by_me: false,
        }],
      }),
      created: false,
    });
    renderPage();

    const select = await screen.findByLabelText('Scout for team 254 in QM 1');
    const option = within(select).getByRole('option', { name: /Sam/ }) as HTMLOptionElement;
    expect(option.disabled).toBe(true);
  });

  it("gives a member their own list and no editing controls", async () => {
    signInTestWorkspace('member', { id: 2, display_name: 'Sam' });
    api.ensureTeamRoom.mockResolvedValue({
      ...snapshot({
        me: MEMBERS[1],
        assignments: [{
          match_key: '2026week0_qm1',
          team_key: 'frc1114',
          assigned_member_id: 2,
          assigned_display_name: 'Sam',
          member_active: true,
          covered: false,
          covered_by_me: false,
        }],
      }),
      created: false,
    });
    renderPage();

    expect(await screen.findByText('Your assignments')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Scout team 1114' })).toHaveAttribute(
      'href',
      '/scouting?event=2026week0&match=2026week0_qm1&team=frc1114',
    );
    expect(screen.queryByRole('button', { name: 'Auto-assign' })).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/Scout for team/)).not.toBeInTheDocument();
  });

  it('confirms auto-assign before saving it in one go', async () => {
    signInTestWorkspace('leader');
    api.ensureTeamRoom.mockResolvedValue({ ...snapshot(), created: false });
    api.saveTeamRoomAssignments.mockResolvedValue(snapshot());
    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: 'Auto-assign' }));
    expect(await screen.findByText(/Nobody gets two teams in the same match/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Save 2' }));
    await waitFor(() => expect(api.saveTeamRoomAssignments).toHaveBeenCalledTimes(1));
    const [, changes] = api.saveTeamRoomAssignments.mock.calls[0];
    expect(changes).toHaveLength(2);
    expect(new Set(changes.map((c: { assigned_member_id: number }) => c.assigned_member_id))).toEqual(new Set([1, 2]));
  });
});
