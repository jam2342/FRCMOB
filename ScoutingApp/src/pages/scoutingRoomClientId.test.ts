import { beforeEach, describe, expect, it } from 'vitest';
import { getOrCreateScoutingRoomClientId } from './scoutingRoomClientId';

describe('scouting room client id', () => {
  beforeEach(() => {
    window.sessionStorage.removeItem('scouting_room_client_id_v1');
  });

  it('reuses one session-scoped client id across calls', () => {
    const first = getOrCreateScoutingRoomClientId();
    const second = getOrCreateScoutingRoomClientId();
    expect(first).toBeTruthy();
    expect(second).toBe(first);
  });

  it('normalizes malformed stored values before reuse', () => {
    window.sessionStorage.setItem('scouting_room_client_id_v1', '  CLIENT @@@ 42  ');
    const value = getOrCreateScoutingRoomClientId();
    expect(value).toBe('client42');
  });
});

