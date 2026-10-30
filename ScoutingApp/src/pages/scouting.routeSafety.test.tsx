// Shared mocks: scoutingPage.mocks.testkit.ts. Room leader controls: scouting.roomLeader.test.tsx.
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ScoutingPage } from './ScoutingPage';
import { signInTestWorkspace } from '../test/workspace';

vi.mock('../api', async () => (await import('./scoutingPage.mocks.testkit')).apiModule());
vi.mock('../hooks/useOnlineStatus', async () => (await import('./scoutingPage.mocks.testkit')).onlineStatusModule);

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
});
