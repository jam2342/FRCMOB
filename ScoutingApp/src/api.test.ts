import { afterEach, describe, expect, it, vi } from 'vitest';

describe('scoutingRoomWebSocketUrl', () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllEnvs();
    vi.resetModules();
    window.localStorage.clear();
  });

  it('preserves API pathname prefixes when building websocket URLs', async () => {
    vi.stubEnv('VITE_API_URL', 'https://scouting.example.com/api');
    const api = await import('./api');
    api.setClientAdminModeEnabled(false);

    const url = api.scoutingRoomWebSocketUrl('Room-ABC', {
      scout_profile: 'Jamal',
      history_limit: 220,
    });
    const parsed = new URL(url);

    expect(parsed.protocol).toBe('wss:');
    expect(parsed.pathname).toBe('/api/scouting/rooms/room-abc/ws');
    expect(parsed.searchParams.get('scout_profile')).toBe('Jamal');
    expect(parsed.searchParams.get('history_limit')).toBe('220');
  });

  it('uses explicit websocket base URL when configured', async () => {
    vi.stubEnv('VITE_API_URL', '/api');
    vi.stubEnv('VITE_WS_URL', 'wss://ws.example.com/api');
    const api = await import('./api');
    api.setClientAdminModeEnabled(false);

    const url = api.scoutingRoomWebSocketUrl('Room-ABC');
    const parsed = new URL(url);

    expect(parsed.protocol).toBe('wss:');
    expect(parsed.host).toBe('ws.example.com');
    expect(parsed.pathname).toBe('/api/scouting/rooms/room-abc/ws');
  });

  it('does not duplicate api prefix for root-relative requests', async () => {
    vi.stubEnv('VITE_API_URL', '/api');
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ events: [] }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    vi.stubGlobal('fetch', fetchMock as unknown as typeof fetch);
    const api = await import('./api');

    await api.searchEvents('2026');

    const calledUrl = String(fetchMock.mock.calls[0]?.[0] || '');
    expect(calledUrl).toContain('/api/events/search?');
    expect(calledUrl).not.toContain('/api/api/');
  });

  it('surfaces plain-text backend errors without response stream reuse crashes', async () => {
    vi.stubEnv('VITE_API_URL', '/api');
    const fetchMock = vi.fn().mockResolvedValue(
      new Response('Internal Server Error', {
        status: 500,
        headers: { 'Content-Type': 'text/plain' },
      }),
    );
    vi.stubGlobal('fetch', fetchMock as unknown as typeof fetch);
    const api = await import('./api');

    await expect(
      api.createOrJoinScoutingRoom({
        room_key: 'room-test',
        scout_profile: 'Scout A',
      }),
    ).rejects.toThrow('Internal Server Error');
  });

  it('normalizes body-stream-read errors into a status-based fallback', async () => {
    vi.stubEnv('VITE_API_URL', '/api');
    const fetchMock = vi.fn().mockResolvedValue(
      new Response("Failed to execute 'text' on 'Response': body stream already read", {
        status: 500,
        headers: { 'Content-Type': 'text/plain' },
      }),
    );
    vi.stubGlobal('fetch', fetchMock as unknown as typeof fetch);
    const api = await import('./api');

    await expect(
      api.createOrJoinScoutingRoom({
        room_key: 'room-test',
        scout_profile: 'Scout A',
      }),
    ).rejects.toThrow('Request failed with status 500');
  });

  it('turns room request timeouts into readable errors', async () => {
    vi.useFakeTimers();
    vi.stubEnv('VITE_API_URL', '/api');
    const fetchMock = vi.fn((_input: RequestInfo | URL, init?: RequestInit) => new Promise<Response>((_resolve, reject) => {
      const signal = init?.signal as AbortSignal | undefined;
      if (!signal) {
        reject(new Error('Missing request signal.'));
        return;
      }
      const rejectWithReason = () => {
        const reason = signal.reason;
        if (reason instanceof Error) {
          reject(reason);
          return;
        }
        if (typeof reason === 'string' && reason.trim()) {
          reject(new Error(reason));
          return;
        }
        reject(new DOMException('signal is aborted without reason', 'AbortError'));
      };
      if (signal.aborted) {
        rejectWithReason();
        return;
      }
      signal.addEventListener('abort', rejectWithReason, { once: true });
    }));
    vi.stubGlobal('fetch', fetchMock as unknown as typeof fetch);
    const api = await import('./api');

    const request = api.createOrJoinScoutingRoom({
      room_key: 'room-test',
      scout_profile: 'Scout A',
      timeoutMs: 2000,
    });
    const expectation = expect(request).rejects.toThrow('Request timed out after 2s.');

    await vi.advanceTimersByTimeAsync(2000);

    await expectation;
  });

  it('passes room access token via header and strips token from request body', async () => {
    vi.stubEnv('VITE_API_URL', '/api');
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          ok: true,
          room: {
            room_key: 'room-test',
            event_key: null,
            title: null,
            created_by: 'Scout A',
            archived: false,
            created_at: null,
            updated_at: null,
            last_activity_at: null,
            presence: [],
            ws_path: '/scouting/rooms/room-test/ws',
          },
          entries: [],
        }),
        {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        },
      ),
    );
    vi.stubGlobal('fetch', fetchMock as unknown as typeof fetch);
    const api = await import('./api');

    await api.createOrJoinScoutingRoom({
      room_key: 'room-test',
      scout_profile: 'Scout A',
      client_id: 'client-qa',
      create_if_missing: false,
      room_access_token: 'token-room-abc',
    });

    const requestInit = fetchMock.mock.calls[0]?.[1] as RequestInit | undefined;
    const headers = new Headers(requestInit?.headers);
    expect(headers.get('X-Room-Access-Token')).toBe('token-room-abc');
    const body = JSON.parse(String(requestInit?.body || '{}')) as Record<string, unknown>;
    expect(body.room_key).toBe('room-test');
    expect(body.client_id).toBe('client-qa');
    expect(body.create_if_missing).toBe(false);
    expect(body.room_access_token).toBeUndefined();
  });

  it('passes schedule paging and lightweight flags to the backend', async () => {
    vi.stubEnv('VITE_API_URL', '/api');
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          ok: true,
          event_key: '2026txaus',
          event_name: 'Austin',
          source: 'local',
          published: true,
          times_published: true,
          count: 0,
          matches: [],
        }),
        {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        },
      ),
    );
    vi.stubGlobal('fetch', fetchMock as unknown as typeof fetch);
    const api = await import('./api');

    await api.getEventSchedule('2026txaus', false, {
      includeLiveResults: false,
      includeTeams: false,
      includeTeamNicknames: false,
      limit: 1,
      offset: 3,
    });

    const calledUrl = new URL(String(fetchMock.mock.calls[0]?.[0] || ''), 'https://example.test');
    expect(calledUrl.pathname).toBe('/api/matches/event/2026txaus/schedule');
    expect(calledUrl.searchParams.get('includeLiveResults')).toBe('false');
    expect(calledUrl.searchParams.get('includeTeams')).toBe('false');
    expect(calledUrl.searchParams.get('includeTeamNicknames')).toBe('false');
    expect(calledUrl.searchParams.get('limit')).toBe('1');
    expect(calledUrl.searchParams.get('offset')).toBe('3');
  });

  it('falls back to seasonal search when suggested events endpoint fails', async () => {
    vi.stubEnv('VITE_API_URL', '/api');
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/api/events/suggested?')) {
        return Promise.resolve(
          new Response('Gateway timeout', {
            status: 504,
            headers: { 'Content-Type': 'text/plain' },
          }),
        );
      }
      if (url.includes('/api/events/search?') && url.includes('q=2026')) {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              ok: true,
              query: '2026',
              count: 1,
              events: [
                {
                  event_key: '2026mimtp',
                  name: 'FIM District Mt Pleasant Event presented by AT&T',
                  year: 2026,
                },
              ],
            }),
            {
              status: 200,
              headers: { 'Content-Type': 'application/json' },
            },
          ),
        );
      }
      if (url.includes('/api/events/search?') && url.includes('q=2025')) {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              ok: true,
              query: '2025',
              count: 1,
              events: [
                {
                  event_key: '2025txcmp',
                  name: 'Texas District Championship',
                  year: 2025,
                },
              ],
            }),
            {
              status: 200,
              headers: { 'Content-Type': 'application/json' },
            },
          ),
        );
      }
      return Promise.resolve(
        new Response('Not found', {
          status: 404,
          headers: { 'Content-Type': 'text/plain' },
        }),
      );
    });
    vi.stubGlobal('fetch', fetchMock as unknown as typeof fetch);
    const api = await import('./api');

    const payload = await api.getSuggestedEvents(2026, 2025, 10);

    expect(payload.ok).toBe(true);
    expect(payload.source).toBe('search_fallback');
    expect(payload.count).toBe(2);
    expect(payload.events.map((event) => event.event_key)).toEqual(['2026mimtp', '2025txcmp']);
  });

  it('opens a cooldown circuit after suggested-events failure to avoid repeated failing calls', async () => {
    vi.stubEnv('VITE_API_URL', '/api');
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/api/events/suggested?')) {
        return Promise.resolve(
          new Response('Gateway timeout', {
            status: 504,
            headers: { 'Content-Type': 'text/plain' },
          }),
        );
      }
      if (url.includes('/api/events/search?')) {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              ok: true,
              query: '2026',
              count: 1,
              events: [
                {
                  event_key: '2026mimtp',
                  name: 'FIM District Mt Pleasant Event presented by AT&T',
                  year: 2026,
                },
              ],
            }),
            {
              status: 200,
              headers: { 'Content-Type': 'application/json' },
            },
          ),
        );
      }
      return Promise.resolve(
        new Response('Not found', {
          status: 404,
          headers: { 'Content-Type': 'text/plain' },
        }),
      );
    });
    vi.stubGlobal('fetch', fetchMock as unknown as typeof fetch);
    const api = await import('./api');

    const first = await api.getSuggestedEvents(2026, 2025, 10);
    const second = await api.getSuggestedEvents(2026, 2025, 10);

    expect(first.source).toBe('search_fallback');
    expect(second.source).toBe('search_fallback');
    const suggestedCalls = fetchMock.mock.calls.filter((call) =>
      String(call[0]).includes('/api/events/suggested?'),
    );
    expect(suggestedCalls.length).toBe(1);
  });
});
