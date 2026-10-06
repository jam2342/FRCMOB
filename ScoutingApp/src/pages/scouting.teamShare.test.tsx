// Team-room sharing and the scouting setup labels, kept apart from the room-leader tests so that
// file stays as quick as it was (its leader test is timing-sensitive under parallel suites).
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ScoutingPage } from './ScoutingPage';
import { saveScoutingRoomEntry, getEventSchedule, ensureTeamRoom } from '../api';
import { normalizeEntry, scoutingEntriesStorageKey } from './scoutingPage.helpers';
import { signInTestWorkspace } from '../test/workspace';

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
  saveScoutingRoomEntry: vi.fn(async (_roomKey: string, { entry }: { entry: unknown }) => ({
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

function teamRoomFor(roomKey: string, eventKey: string) {
  return { ok: true, created: false, room_key: roomKey, event_key: eventKey, me: { member_id: 1, display_name: 'Test Scout', role: 'leader' }, members: [], assignments: [] } as never;
}

describe('Scouting team-room sharing and labels', () => {
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

  it("shares this event's phone-only reports when the team room connects, keeping their ids", async () => {
    localStorage.setItem('team_room_keys_v1', JSON.stringify(['room-test']));
    sessionStorage.setItem('scouting_room_active_key_v1', 'room-test');
    const entry = normalizeEntry({id:'phone-only',event_key:'2026week0',match_key:'2026week0_qm1',team_key:'frc254',scout_profile:'Test Scout'})!;
    localStorage.setItem(scoutingEntriesStorageKey(1), JSON.stringify([
      entry,
      {...entry,id:'already-shared',server_synced:true},
      {...entry,id:'another-room',room_key:'other-room'},
      {...entry,id:'another-event',event_key:'2026other'},
    ]));
    vi.mocked(ensureTeamRoom).mockResolvedValue(teamRoomFor('room-test', '2026week0'));
    const callsBefore = vi.mocked(saveScoutingRoomEntry).mock.calls.length;
    render(<MemoryRouter initialEntries={['/scouting?event=2026week0']}><ScoutingPage /></MemoryRouter>);
    await screen.findByText('Shared 1 saved report with your team.', undefined, FIND_TIMEOUT);
    expect(vi.mocked(saveScoutingRoomEntry).mock.calls.slice(callsBefore)).toHaveLength(1);
    expect(saveScoutingRoomEntry).toHaveBeenLastCalledWith('room-test', expect.objectContaining({client_entry_id:'phone-only'}));
    // The entries are written to storage just after the message renders.
    await waitFor(() => {
      const stored = JSON.parse(localStorage.getItem(scoutingEntriesStorageKey(1))!);
      expect(stored.find((row: {id:string}) => row.id === 'phone-only')).toMatchObject({room_key:'room-test',server_synced:true});
    });
  }, HEAVY_RENDER_TIMEOUT_MS);

  it("never shares into a room that isn't this event's team room", async () => {
    // The room still active belongs to the event the scout just left.
    localStorage.setItem('team_room_keys_v1', JSON.stringify(['room-test', 'room-next']));
    sessionStorage.setItem('scouting_room_active_key_v1', 'room-test');
    vi.mocked(ensureTeamRoom).mockResolvedValue(teamRoomFor('room-next', '2026week0'));
    const entry = normalizeEntry({id:'phone-only',event_key:'2026week0',match_key:'2026week0_qm1',team_key:'frc254',scout_profile:'Test Scout'})!;
    localStorage.setItem(scoutingEntriesStorageKey(1), JSON.stringify([entry]));
    const callsBefore = vi.mocked(saveScoutingRoomEntry).mock.calls.length;
    render(<MemoryRouter initialEntries={['/scouting?event=2026week0']}><ScoutingPage /></MemoryRouter>);
    await new Promise((resolve) => setTimeout(resolve, 1500));
    expect(vi.mocked(saveScoutingRoomEntry).mock.calls.slice(callsBefore).filter(([room]) => room === 'room-test')).toHaveLength(0);
  }, HEAVY_RENDER_TIMEOUT_MS);

  it('shows readable station names and visible mobile quick-action text', async () => {
    vi.stubGlobal('matchMedia', () => ({matches:true,addEventListener:vi.fn(),removeEventListener:vi.fn()}));
    vi.mocked(getEventSchedule).mockResolvedValue({ok:true,event_key:'2026week0',event_name:'Week 0',count:1,matches:[{
      match_key:'2026week0_qm1',display_name:'Qualification 1',comp_level:'qm',set_number:1,match_number:1,
      red:[{team_key:'frc9427',team_number:9427,station:'r1'}],blue:[{team_key:'frc254',team_number:254,station:'b2'}],
    }]} as never);
    render(<MemoryRouter initialEntries={['/scouting?event=2026week0&match=2026week0_qm1']}><ScoutingPage /></MemoryRouter>);
    await screen.findByText('Red 1', undefined, FIND_TIMEOUT);
    expect(screen.getByText('Blue 2')).toBeInTheDocument();
    expect(screen.getByRole('button',{name:'Jump to next match'})).toHaveTextContent('Next match');
    expect(screen.getByRole('button',{name:'Jump to next team'})).toHaveTextContent('Next team');
  }, HEAVY_RENDER_TIMEOUT_MS);

});
