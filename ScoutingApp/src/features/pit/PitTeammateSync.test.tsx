import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { PitScoutingEntry } from '../../api';

vi.mock('../../components/PageViewBar', () => ({ PageViewBar: () => null }));
vi.mock('../../components/EventPicker', () => ({ EventPicker: () => null }));
vi.mock('../workspace/WorkspaceGate', () => ({ WorkspaceGate: ({ children }: { children: React.ReactNode }) => children }));
vi.mock('../workspace/useWorkspace', () => ({ useWorkspace: () => ({ workspace: { id: 1 }, me: { id: 7, role: 'member', display_name: 'Priya' } }) }));
vi.mock('../../hooks/useOnlineStatus', () => ({ useOnlineStatus: () => ({ online: true }) }));
vi.mock('../../utils/offlineQueue', () => ({ listQueuedMutations: vi.fn(async () => []) }));
vi.mock('../../api', async (original) => ({
  ...(await original<typeof import('../../api')>()),
  getEventTeamsIntel: vi.fn(async () => ({ teams: [
    { team_key: 'frc100', team_number: 100, nickname: 'Alpha' },
    { team_key: 'frc200', team_number: 200, nickname: 'Bravo' },
    { team_key: 'frc300', team_number: 300, nickname: 'Charlie' },
  ] })),
  listPitEntries: vi.fn(),
  upsertPitEntry: vi.fn(),
  getPitAssignments: vi.fn(async () => []),
}));
import { PitScoutingPage } from '../../pages/PitScoutingPage';
import { HttpStatusError, QueuedForSyncError, listPitEntries, upsertPitEntry } from '../../api';
import { refreshActivePolls } from '../../hooks/useSingleFlightPolling';
import { createMemoryPitPhotoStore, enqueuePitPhoto, flushPitPhotos, listQueuedPitPhotos, setPitPhotoStore } from '../offline/pitPhotoQueue';

const entry = (teamKey: string, payload: Record<string, unknown>, updated = '2026-11-11T10:00:00Z') =>
  ({ id: 1, event_key: '2026old', team_key: teamKey, scout_profile: 'Alex', payload, photos: [], created_at: null, updated_at: updated }) as PitScoutingEntry;
const serverHas = (...entries: PitScoutingEntry[]) => vi.mocked(listPitEntries).mockResolvedValue({ ok: true, entries });

beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn();
  setPitPhotoStore(createMemoryPitPhotoStore());
  URL.createObjectURL = vi.fn(() => 'blob:thumb');
  URL.revokeObjectURL = vi.fn();
  // mockReset, not just clear: a one-off answer a test left unused must not reach the next test.
  vi.mocked(listPitEntries).mockReset();
  serverHas();
  vi.mocked(upsertPitEntry).mockImplementation(async (body) => ({ ok: true, entry: entry(body.team_key, body.payload as Record<string, unknown>, new Date().toISOString()) }));
});
afterEach(() => { cleanup(); localStorage.clear(); vi.clearAllMocks(); });

const renderPage = async () => {
  render(<MemoryRouter initialEntries={['/?event=2026old']}><PitScoutingPage /></MemoryRouter>);
  await screen.findByRole('button', { name: /Team 100/ });
};
const poll = () => act(async () => { await refreshActivePolls(); });

it('a team a teammate scouts leaves Left, and Save & next no longer sends me to it', async () => {
  await renderPage();
  expect(screen.getByRole('tab', { name: 'Left, 3' })).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: /Team 100/ }));
  // Alex finishes 200 on another phone.
  serverHas(entry('frc200', { notes: 'swerve' }));
  await poll();
  fireEvent.change(screen.getAllByRole('spinbutton')[0], { target: { value: '5' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save & next' }));
  await act(async () => {});
  expect(screen.getByRole('heading', { name: 'Team 300' })).toBeInTheDocument();
  expect(screen.getByRole('status')).toHaveTextContent('Saved #100. Now on #300.');
});

it("shows a teammate's notes on the open team when nothing is typed, and warns instead when something is", async () => {
  await renderPage();
  fireEvent.click(screen.getByRole('button', { name: /Team 100/ }));
  serverHas(entry('frc100', { notes: 'from Alex' }));
  await poll();
  expect(screen.getByRole('status')).toHaveTextContent("A teammate just saved notes for #100. You're seeing them now.");
  expect(screen.getByDisplayValue('from Alex')).toBeInTheDocument();
  // Now type, then Alex saves again: my typing stays and I'm told what Save would do.
  fireEvent.change(screen.getByDisplayValue('from Alex'), { target: { value: 'mine' } });
  serverHas(entry('frc100', { notes: 'Alex again' }, '2026-11-11T10:05:00Z'));
  await poll();
  expect(screen.getByDisplayValue('mine')).toBeInTheDocument();
  expect(screen.getByRole('status')).toHaveTextContent('Saving yours replaces theirs.');
});

it("a poll that started before my save can't put the old copy back", async () => {
  await renderPage();
  fireEvent.click(screen.getByRole('button', { name: /Team 100/ }));
  let answer: (value: { ok: boolean; entries: PitScoutingEntry[] }) => void = () => {};
  vi.mocked(listPitEntries).mockReturnValueOnce(new Promise((resolve) => { answer = resolve; }));
  const calls = vi.mocked(listPitEntries).mock.calls.length;
  const slowPoll = refreshActivePolls();
  // A refresh waits for any run already in flight, so give it time to start.
  await waitFor(() => expect(listPitEntries).toHaveBeenCalledTimes(calls + 1));
  fireEvent.change(screen.getAllByRole('spinbutton')[0], { target: { value: '5' } });
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Save' })); });
  expect(upsertPitEntry).toHaveBeenCalledTimes(1);
  // The poll answers with the copy from before the save.
  await act(async () => { answer({ ok: true, entries: [] }); await slowPoll; });
  expect(screen.getByRole('tab', { name: 'Done, 1' })).toBeInTheDocument();
});

it('my own queued save showing up in a poll is not credited to a teammate', async () => {
  await renderPage();
  fireEvent.click(screen.getByRole('button', { name: /Team 100/ }));
  fireEvent.change(screen.getAllByRole('spinbutton')[0], { target: { value: '5' } });
  vi.mocked(upsertPitEntry).mockRejectedValueOnce(new QueuedForSyncError('queued'));
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Save' })); });
  await act(async () => {});
  // The queue delivered it (nothing left in it), and the server now has exactly what this phone sent.
  expect(screen.getByRole('status')).toHaveTextContent('Synced with your team.');
  serverHas(entry('frc100', vi.mocked(upsertPitEntry).mock.calls[0][0].payload as Record<string, unknown>));
  await poll();
  expect(screen.getByRole('status')).toHaveTextContent('Synced with your team.');
});

it('a photo the server refused says why and can be tried again, without holding up the rest', async () => {
  await renderPage();
  fireEvent.click(screen.getByRole('button', { name: /Team 100/ }));
  await act(async () => {
    await enqueuePitPhoto({ blob: new Blob(['a'], { type: 'image/jpeg' }), workspaceId: 1, eventKey: '2026old', teamKey: 'frc100' }, 1);
    await enqueuePitPhoto({ blob: new Blob(['b'], { type: 'image/jpeg' }), workspaceId: 1, eventKey: '2026old', teamKey: 'frc200' }, 2);
    await flushPitPhotos(async (row) => {
      if (row.teamKey === 'frc100') throw new HttpStatusError(400, 'Photo limit reached (12 per team). Delete one first.');
      return entry('frc200', {});
    }, { workspaceId: 1 });
  });
  expect(screen.getByText('Not uploaded')).toBeInTheDocument();
  const alert = screen.getByRole('alert');
  expect(alert).toHaveTextContent("A photo on this phone wasn't accepted: Photo limit reached (12 per team). Delete one first.");
  expect(within(screen.getByRole('group', { name: 'Save pit entry' })).getByRole('status')).toHaveTextContent('Not saved yet · 1 photo not uploaded');
  // The other team's photo went through.
  expect((await listQueuedPitPhotos()).map((row) => row.teamKey)).toEqual(['frc100']);
  await act(async () => { fireEvent.click(within(alert).getByRole('button', { name: 'Try again' })); });
  expect(screen.queryByText('Not uploaded')).not.toBeInTheDocument();
  expect(screen.getByText('Waiting to upload')).toBeInTheDocument();
});
