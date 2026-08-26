import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import { ScoutingPage } from './ScoutingPage';

vi.mock('../api', () => ({
  addScoutingRoomSecondaryLeader: vi.fn(async () => ({
    ok: true,
    room_key: 'room-test',
    target_scout_profile: 'Scout B',
    secondary_leader_scout_profiles: ['Scout B'],
    created: true,
  })),
  clearStoredRoomAccessToken: vi.fn(),
  createOrJoinScoutingRoom: vi.fn(async () => ({
    room: {
      room_key: 'room-test',
      event_key: '2026week0',
      title: 'Test Room',
      created_at: Date.now(),
      updated_at: Date.now(),
      created_by: 'Scout A',
      secondary_leader_scout_profiles: [],
      presence: [],
    },
    entries: [],
  })),
  getEventSchedule: vi.fn(async () => ({
    ok: true,
    event_key: '2026week0',
    event_name: 'Week 0',
    count: 0,
    matches: [],
  })),
  getEventTeamsIntel: vi.fn(async () => ({
    ok: true,
    event_key: '2026week0',
    count: 0,
    teams: [],
  })),
  getSuggestedEvents: vi.fn(async () => ({
    ok: true,
    preferred_year: 2026,
    fallback_year: 2025,
    selected_year: 2026,
    source: 'test',
    count: 0,
    events: [],
  })),
  getStoredRoomAccessToken: vi.fn(() => 'token-test'),
  getTeamIntel: vi.fn(async () => ({})),
  removeScoutingRoomSecondaryLeader: vi.fn(async () => ({
    ok: true,
    room_key: 'room-test',
    target_scout_profile: 'Scout B',
    secondary_leader_scout_profiles: [],
    removed: true,
  })),
  saveScoutingRoomEntry: vi.fn(async ({ entry }: { entry: unknown }) => ({
    ok: true,
    entry: {
      room_key: 'room-test',
      entry,
    },
  })),
  setStoredRoomAccessToken: vi.fn(),
  scoutingRoomWebSocketUrl: vi.fn(() => 'ws://localhost/test-room'),
}));

// ScoutingPage is 4,500 lines and mounts the whole live-scouting workspace, so
// a full render runs 1.4s on an idle machine and over 9s when the suite is
// competing for CPU. Under vitest's 5s default that reads as a failed assertion
// rather than as "the box was busy", which is what it actually is.
const HEAVY_RENDER_TIMEOUT_MS = 20_000;

describe('Scouting route safety', () => {
  it('renders scouting page in standard router context without crashing', async () => {
    render(
      <MemoryRouter initialEntries={['/scouting']}>
        <Routes>
          <Route path="/scouting" element={<ScoutingPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText('Scouting Mode')).toBeInTheDocument();
    expect(screen.getByText('Save + Data')).toBeInTheDocument();
    expect(screen.getByText('Match Timer')).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Setup' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Room' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Data' })).toBeInTheDocument();
  }, HEAVY_RENDER_TIMEOUT_MS);

  it('does not clear session room key during page mount', async () => {
    window.sessionStorage.setItem('scouting_room_active_key_v1', 'room-persist');
    render(
      <MemoryRouter initialEntries={['/scouting']}>
        <Routes>
          <Route path="/scouting" element={<ScoutingPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText('Scouting Mode')).toBeInTheDocument();
    expect(window.sessionStorage.getItem('scouting_room_active_key_v1')).toBe('room-persist');
  }, HEAVY_RENDER_TIMEOUT_MS);
});
