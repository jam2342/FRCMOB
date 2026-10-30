// Room leader controls on the scouting page. Split from scouting.routeSafety.test.tsx:
// these two are the slowest page tests and ran one after the other there.
// Shared mocks: scoutingPage.mocks.testkit.ts.
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ScoutingPage } from './ScoutingPage';
import { addScoutingRoomSecondaryLeader, removeScoutingRoomSecondaryLeader } from '../api';
import { signInTestWorkspace } from '../test/workspace';
import { closeToast, currentToast } from '../components/ui/toast';

vi.mock('../api', async () => (await import('./scoutingPage.mocks.testkit')).apiModule());
vi.mock('../hooks/useOnlineStatus', async () => (await import('./scoutingPage.mocks.testkit')).onlineStatusModule);

// ScoutingPage is 4,500 lines and mounts the whole live-scouting workspace, so
// a full render runs 1.4s on an idle machine and over 9s when the suite is
// competing for CPU. Under vitest's 5s default that reads as a failed assertion
// rather than as "the box was busy", which is what it actually is.
const HEAVY_RENDER_TIMEOUT_MS = 20_000;
// Each wait inside a test gets most of that budget too, for the same reason.
const FIND_TIMEOUT = { timeout: 15_000 };

describe('Scouting room leader', () => {
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
