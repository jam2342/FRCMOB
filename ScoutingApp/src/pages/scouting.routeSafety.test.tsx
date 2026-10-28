// findBy* waits get 5 s: CI runners missed the 1 s default.
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ScoutingPage } from './ScoutingPage';
import { addScoutingRoomSecondaryLeader, removeScoutingRoomSecondaryLeader } from '../api';
import { signInTestWorkspace } from '../test/workspace';
import { closeToast, currentToast } from '../components/ui/toast';

vi.mock('../api', () => ({
  clientAdminKeyAvailable: () => false,
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
      presence: [{ scout_profile: 'Scout A', connections: 1 }, { scout_profile: 'Scout B', connections: 1 }],
      room_role: 'owner',
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
  // No team room in these tests: they drive the keyed room path.
  ensureTeamRoom: vi.fn(async () => { throw new Error('no team room in this test'); }),
  getTeamRoom: vi.fn(async () => null),
}));

// The online monitor pings the real API; when that answer lands mid-test it
// re-renders the page at a random moment. These tests are about rooms.
vi.mock('../hooks/useOnlineStatus', () => ({
  useOnlineStatus: () => ({ online: true, queueSize: 0, isShowingOfflineData: false }),
}));

// ScoutingPage is 4,500 lines and mounts the whole live-scouting workspace, so
// a full render runs 1.4s on an idle machine and over 9s when the suite is
// competing for CPU. Under vitest's 5s default that reads as a failed assertion
// rather than as "the box was busy", which is what it actually is.
const HEAVY_RENDER_TIMEOUT_MS = 20_000;
// Each wait inside a test gets most of that budget too, for the same reason.
const FIND_TIMEOUT = { timeout: 15_000 };

describe('Scouting route safety', () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
    signInTestWorkspace();
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { value: vi.fn(), configurable: true });
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    Reflect.deleteProperty(HTMLElement.prototype, 'scrollIntoView');
  });
  it('renders scouting page in standard router context without crashing', async () => {
    render(
      <MemoryRouter initialEntries={['/scouting']}>
        <Routes>
          <Route path="/scouting" element={<ScoutingPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText('Scouting Mode', undefined, FIND_TIMEOUT)).toBeInTheDocument();
    expect(screen.getByText('Save + Data')).toBeInTheDocument();
    expect(screen.getByText('Match Timer')).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Robot' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Room' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Saved' })).toBeInTheDocument();
  }, HEAVY_RENDER_TIMEOUT_MS);

  it("uses the workspace name even if this device saved an older scout name", async () => {
    localStorage.setItem('scouting_manual_profile_v1', 'Old Name');
    render(
      <MemoryRouter initialEntries={['/scouting']}>
        <Routes>
          <Route path="/scouting" element={<ScoutingPage />} />
        </Routes>
      </MemoryRouter>,
    );

    const input = await screen.findByLabelText('Scout Name', undefined, FIND_TIMEOUT);
    expect((input as HTMLInputElement).value).toBe('Test Scout');
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

    expect(await screen.findByText('Scouting Mode', undefined, FIND_TIMEOUT)).toBeInTheDocument();
    expect(window.sessionStorage.getItem('scouting_room_active_key_v1')).toBe('room-persist');
  }, HEAVY_RENDER_TIMEOUT_MS);
  it('promotes and removes a secondary leader, restoring controls after failures', async () => {
    vi.stubGlobal('WebSocket', class {
      static OPEN = 1;
      readyState = 1;
      send = vi.fn();
      close = vi.fn();
    });
    localStorage.setItem('scouting_manual_profile_v1', 'Scout A');
    render(<MemoryRouter><ScoutingPage /></MemoryRouter>);
    fireEvent.click(await screen.findByRole('tab', { name: 'Room' }, FIND_TIMEOUT));
    fireEvent.change(screen.getByLabelText('Scouting room key'), { target: { value: 'room-test' } });
    fireEvent.click(screen.getByRole('button', { name: 'Join Room' }));

    vi.mocked(addScoutingRoomSecondaryLeader).mockRejectedValueOnce(new Error('Promotion rejected'));
    fireEvent.click(await screen.findByRole('button', { name: 'Promote Scout B' }, FIND_TIMEOUT));
    expect(await screen.findByText('Promotion rejected', undefined, FIND_TIMEOUT)).toBeInTheDocument();
    const promote = screen.getByRole('button', { name: 'Promote Scout B' });
    expect(promote).toBeEnabled();
    fireEvent.click(promote);
    const remove = await screen.findByRole('button', { name: 'Remove Leader Scout B' }, FIND_TIMEOUT);
    expect(addScoutingRoomSecondaryLeader).toHaveBeenLastCalledWith('room-test', {
      scout_profile: 'Scout B', room_access_token: 'token-test',
    });
    vi.mocked(removeScoutingRoomSecondaryLeader).mockRejectedValueOnce(new Error('Removal rejected'));
    fireEvent.click(remove);
    expect(await screen.findByText('Removal rejected', undefined, FIND_TIMEOUT)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Remove Leader Scout B' })).toBeEnabled();
    fireEvent.click(screen.getByRole('button', { name: 'Remove Leader Scout B' }));
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Remove Leader Scout B' })).not.toBeInTheDocument());
    expect(removeScoutingRoomSecondaryLeader).toHaveBeenLastCalledWith('room-test', {
      scout_profile: 'Scout B', room_access_token: 'token-test',
    });
  }, HEAVY_RENDER_TIMEOUT_MS);

  it("tells a room leader the moment a scout flags a robot, but not about the leader's own flags", async () => {
    type FakeSocket = { readyState: number; send: ReturnType<typeof vi.fn>; close: ReturnType<typeof vi.fn>; onopen?: () => void; onmessage?: (event: { data: string }) => void };
    const sockets: FakeSocket[] = [];
    vi.stubGlobal('WebSocket', class {
      static OPEN = 1;
      readyState = 1;
      send = vi.fn();
      close = vi.fn();
      constructor() { sockets.push(this as unknown as FakeSocket); }
    });
    localStorage.setItem('scouting_manual_profile_v1', 'Scout A');
    render(<MemoryRouter><ScoutingPage /></MemoryRouter>);
    fireEvent.click(await screen.findByRole('tab', { name: 'Room' }, FIND_TIMEOUT));
    fireEvent.change(screen.getByLabelText('Scouting room key'), { target: { value: 'room-test' } });
    fireEvent.click(screen.getByRole('button', { name: 'Join Room' }));
    await waitFor(() => expect(sockets.length).toBeGreaterThan(0), FIND_TIMEOUT);
    // The owner's role arrives with the join; wait for the leader controls before the flag.
    await screen.findByRole('button', { name: 'Promote Scout B' }, FIND_TIMEOUT);
    const socket = sockets.at(-1)!;
    socket.onopen?.();
    const flag = (scout: string) => ({ data: JSON.stringify({ type: 'live_flag', scout_profile: scout, team_key: 'frc254', match_key: '2026week0_qm3', tag: 'broke_down' }) });

    socket.onmessage?.(flag('Test Scout'));
    expect(currentToast()).toBeNull();
    socket.onmessage?.(flag('Scout B'));
    expect(currentToast()).toMatchObject({ message: 'Scout B: 254 · Broke down · QM 3', tone: 'warning' });
    closeToast(currentToast()!.id);
  }, HEAVY_RENDER_TIMEOUT_MS);

});
