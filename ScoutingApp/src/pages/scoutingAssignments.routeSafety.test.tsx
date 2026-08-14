import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import { ScoutingAssignPage } from './ScoutingAssignPage';

vi.mock('../api', () => ({
  createOrJoinScoutingRoom: vi.fn(async () => ({
    ok: true,
    room: {
      room_key: 'room-test',
      event_key: '2026week0',
      title: 'Test Room',
      created_by: 'Scout A',
      archived: false,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      last_activity_at: new Date().toISOString(),
      leader_scout_profile: 'Scout A',
      leader_source: 'owner_present',
      presence: [{ scout_profile: 'Scout A', connections: 1 }],
      ws_path: '/scouting/rooms/room-test/ws',
      room_role: 'owner',
    },
    entries: [],
    assignments: [],
    my_assignments: [],
    access: {
      room_role: 'owner',
      room_access_token: 'token-test',
      expires_at: new Date(Date.now() + 3600 * 1000).toISOString(),
      expires_at_unix: Math.floor(Date.now() / 1000) + 3600,
      ttl_sec: 3600,
      header: 'X-Room-Access-Token',
    },
  })),
  getEventSchedule: vi.fn(async () => ({
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
        red: [{ team_key: 'frc118', station: 'r1' }, { team_key: 'frc148', station: 'r2' }, { team_key: 'frc3310', station: 'r3' }],
        blue: [{ team_key: 'frc254', station: 'b1' }, { team_key: 'frc1114', station: 'b2' }, { team_key: 'frc1678', station: 'b3' }],
      },
    ],
  })),
  getScoutingRoomAssignments: vi.fn(async () => ({
    ok: true,
    room_key: 'room-test',
    event_key: '2026week0',
    count: 0,
    assignments: [],
    my_assignments: [],
  })),
  getStoredRoomAccessToken: vi.fn(() => 'token-test'),
  setStoredRoomAccessToken: vi.fn(),
  upsertScoutingRoomAssignment: vi.fn(async () => ({
    ok: true,
    room_key: 'room-test',
    deleted: false,
    assignment: {
      match_key: '2026week0_qm1',
      team_key: 'frc118',
      assigned_scout_profile: 'Scout A',
    },
  })),
  replaceScoutingRoomAssignments: vi.fn(async () => ({
    ok: true,
    room_key: 'room-test',
    event_key: '2026week0',
    count: 0,
    assignments: [],
  })),
}));

describe('Scouting assignments route safety', () => {
  it('renders assignments page in standard router context without crashing', async () => {
    render(
      <MemoryRouter initialEntries={['/scouting/assignments?event=2026week0']}>
        <Routes>
          <Route path="/scouting/assignments" element={<ScoutingAssignPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText('Scouting Assignments')).toBeInTheDocument();
    expect(screen.getByText('Scout Roster')).toBeInTheDocument();
    expect(screen.getByText('Live Scouting')).toBeInTheDocument();
  });
});
