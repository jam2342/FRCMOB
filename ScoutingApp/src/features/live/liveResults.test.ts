import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  applyLiveVersion, eventInPlay, liveFreshRead, noteLiveSchedule, resetLiveVersionsForTests, stripLiveVersion, withLiveVersion,
} from './liveVersions';
import { liveSocketCount, startLiveResults } from './liveResults';

class FakeSocket {
  static OPEN = 1;
  static instances: FakeSocket[] = [];
  readyState = 1;
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((message: { data: string }) => void) | null = null;
  onclose: ((event: { code: number }) => void) | null = null;
  url: string;
  constructor(url: string) {
    this.url = url;
    FakeSocket.instances.push(this);
  }
  send(data: string) { this.sent.push(data); }
  close() { this.onclose?.({ code: 1000 }); }
  deliver(payload: unknown) { this.onmessage?.({ data: JSON.stringify(payload) }); }
}

const nowSec = () => Date.now() / 1000;

describe('live results versions', () => {
  beforeEach(() => resetLiveVersionsForTests());

  it('treats an event as in play only while an unfinished match is due within the hour', () => {
    expect(eventInPlay([{ is_completed: false, predicted_time: nowSec() + 600 }])).toBe(true);
    expect(eventInPlay([{ is_completed: true, actual_time: nowSec() - 60 }])).toBe(false);
    expect(eventInPlay([{ is_completed: false, scheduled_time: nowSec() + 86400 }])).toBe(false);
  });

  it('adds the version to live URLs only after a change, and reads fresh briefly', () => {
    const url = '/api/matches/event/2026txhou/schedule?refresh=false';
    expect(applyLiveVersion('2026txhou', 'aaa', 1_000)).toBe(false); // first: remembered only
    expect(withLiveVersion(url, ['2026txhou'])).toBe(url);
    expect(applyLiveVersion('2026txhou', 'bbb', 1_000)).toBe(true);
    const versioned = withLiveVersion(url, ['2026txhou']);
    expect(versioned).toBe(`${url}&lv=bbb`);
    expect(liveFreshRead(versioned, 5_000)).toBe(true);
    expect(liveFreshRead(versioned, 20_000)).toBe(false);
    expect(liveFreshRead(url, 5_000)).toBe(false);
    expect(liveFreshRead('/api/matches/schedules?event_keys=2026abc,2026txhou&lv=bbb', 5_000)).toBe(true);
  });

  it('strips the version from cache keys', () => {
    expect(stripLiveVersion('/a?refresh=false&lv=x')).toBe('/a?refresh=false');
    expect(stripLiveVersion('/a?lv=x&b=1')).toBe('/a?b=1');
    expect(stripLiveVersion('/a?lv=x')).toBe('/a');
    expect(stripLiveVersion('/a?b=1')).toBe('/a?b=1');
  });
});

describe('live results sockets', () => {
  let stop: () => void = () => undefined;

  beforeEach(() => {
    resetLiveVersionsForTests();
    FakeSocket.instances = [];
    vi.stubGlobal('WebSocket', FakeSocket);
    stop = startLiveResults();
  });

  afterEach(() => {
    stop();
    vi.unstubAllGlobals();
  });

  it('opens one socket per in-play event and refreshes polls when results change', () => {
    const updated = vi.fn();
    window.addEventListener('api:updated', updated);
    const live = [{ is_completed: false, predicted_time: nowSec() + 300 }];
    noteLiveSchedule('2026txhou', live);
    noteLiveSchedule('2026txhou', live);
    noteLiveSchedule('2025done', [{ is_completed: true, actual_time: nowSec() - 999999 }]);
    expect(FakeSocket.instances.map((socket) => socket.url)).toEqual([
      expect.stringContaining('/matches/event/2026txhou/live'),
    ]);
    const socket = FakeSocket.instances[0];
    socket.deliver({ type: 'results', event_key: '2026txhou', version: 'v1' });
    expect(updated).not.toHaveBeenCalled();
    socket.deliver({ type: 'results', event_key: '2026txhou', version: 'v2' });
    expect(updated).toHaveBeenCalledTimes(1);
    window.removeEventListener('api:updated', updated);
  });

  it('keeps at most six sockets', () => {
    for (let i = 0; i < 8; i += 1) {
      noteLiveSchedule(`2026ev${i}`, [{ is_completed: false, predicted_time: nowSec() + 60 }]);
    }
    expect(liveSocketCount()).toBe(6);
  });
});
