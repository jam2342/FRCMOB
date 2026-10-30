// Module mocks shared by the ScoutingPage tests (scouting.phone.*, routeSafety,
// roomLeader). The page takes ~1 s per render, so those tests are split across files
// that run in parallel; a single file held the whole suite open for ~26 s. Each test
// file still declares its own vi.mock calls (they are hoisted per file) and loads the
// factories from here. Nothing here may import the app: the factories run while the
// app's own imports are being mocked.
import { vi } from 'vitest';

// The team room the page sees; tests set the snapshot and schedule per case.
export const room = { snapshot: null as unknown, schedule: [] as unknown[], retry: vi.fn(async () => {}) };

export function apiModule() {
  return {
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
  };
}

// The online monitor pings the real API; when that answer lands mid-test it
// re-renders the page at a random moment. These tests are about rooms.
export const onlineStatusModule = {
  useOnlineStatus: () => ({ online: true, queueSize: 0, isShowingOfflineData: false }),
};

export const teamRoomModule = {
  isTeamRoomKey: () => true,
  useTeamRoom: () => ({ snapshot: room.snapshot, schedule: room.schedule, status: 'ready', isLeader: false, retry: room.retry }),
};
