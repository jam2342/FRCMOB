// findBy* waits get 5 s: CI runners missed the 1 s default.
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ScoutingPage } from './ScoutingPage';
import { getEventSchedule } from '../api';
import { signInTestWorkspace, signOutTestWorkspace } from '../test/workspace';

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


const room = vi.hoisted(() => ({ snapshot: null as unknown, schedule: [] as unknown[], retry: vi.fn(async () => {}) }));
vi.mock('../features/workspace/useTeamRoom', () => ({
  isTeamRoomKey: () => true,
  useTeamRoom: () => ({ snapshot: room.snapshot, schedule: room.schedule, status: 'ready', isLeader: false, retry: room.retry }),
}));
vi.mock('../components/PageViewBar', () => ({ PageViewBar: () => null }));

const match = (number: number) => ({
  match_key: `2026week0_qm${number}`, display_name: `Qual ${number}`, comp_level: 'qm', set_number: 1, match_number: number,
  is_completed: false, scheduled_time: null,
  red: [{team_key:'frc118',team_number:118,station:'r1'}, {team_key:'frc254',team_number:254,station:'r2'}], blue: [],
});
const mount = (path = '/scouting?event=2026week0') => render(<MemoryRouter initialEntries={[path]}><ScoutingPage /></MemoryRouter>);
beforeEach(() => {
  localStorage.clear(); sessionStorage.clear();
  room.retry.mockClear();
  signInTestWorkspace('member');
  vi.stubGlobal('matchMedia', () => ({matches:true,addEventListener:vi.fn(),removeEventListener:vi.fn()}));
  Element.prototype.scrollIntoView = vi.fn();
  room.schedule = [match(1),match(2)];
  room.snapshot = {room_key:'room-test', me:{member_id:1}, assignments:[{match_key:'2026week0_qm2',team_key:'frc254',assigned_member_id:1,covered_by_me:false}]};
  vi.mocked(getEventSchedule).mockResolvedValue({matches:room.schedule,event_name:'Week 0'} as never);
});
afterEach(() => {cleanup();vi.unstubAllGlobals();vi.restoreAllMocks();});
it.each([{actual:5, expected:'02:35'}, {actual:null, expected:'02:30'}])('uses actual/predicted starts before a delayed schedule ($expected)', async ({actual, expected}) => {
  vi.stubGlobal('matchMedia', () => ({matches:false,addEventListener:vi.fn(),removeEventListener:vi.fn()}));
  const now = Math.floor(Date.now() / 1000);
  const row = {...match(1), scheduled_time:now - 3600, predicted_time:now - 10, actual_time:actual === null ? null : now - actual};
  room.schedule = [row];
  vi.mocked(getEventSchedule).mockResolvedValue({matches:[row],event_name:'Week 0'} as never);
  const clock = vi.spyOn(Date, 'now').mockReturnValue(now * 1000);
  mount('/scouting?event=2026week0&match=2026week0_qm1&team=frc254');
  const sync = await screen.findByRole('button', {name:/Sync to Match Clock/});
  await waitFor(() => expect(getEventSchedule).toHaveBeenCalled());
  fireEvent.click(sync);
  expect(screen.getAllByText(expected).length).toBeGreaterThan(0);
  clock.mockRestore();
});
it('announces RP selection and shows its selected indicator', async () => {
  vi.stubGlobal('matchMedia', () => ({matches:false,addEventListener:vi.fn(),removeEventListener:vi.fn()}));
  mount('/scouting?event=2026week0&match=2026week0_qm1&team=frc254');
  const rp = await screen.findByRole('button', {name:/Energized RP/});
  expect(rp).toHaveAttribute('aria-pressed','false');
  fireEvent.click(rp);
  expect(rp).toHaveAttribute('aria-pressed','true');
  expect(rp).toHaveTextContent('✓');
});
it('gives an anonymous phone visitor one scout-name label and no unrelated room status', async () => {
  signOutTestWorkspace();
  localStorage.removeItem('scouting_manual_profile_v1');
  room.snapshot = null;
  mount();
  await screen.findByRole('heading', {name:'Scout name'});
  expect(screen.getAllByText('Scout name')).toHaveLength(1);
  expect(screen.getByLabelText('Scout name')).toHaveAttribute('id','setup-scout-name');
  expect(screen.queryByLabelText('Room connection')).not.toBeInTheDocument();
  expect(screen.queryByText('Not connected')).not.toBeInTheDocument();
});
