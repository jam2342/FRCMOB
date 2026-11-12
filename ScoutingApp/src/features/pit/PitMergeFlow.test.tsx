import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { PitConflict, PitScoutingEntry } from '../../api';

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
  ] })),
  listPitEntries: vi.fn(),
  upsertPitEntry: vi.fn(),
  getPitAssignments: vi.fn(async () => []),
}));
import { PitScoutingPage } from '../../pages/PitScoutingPage';
import { listPitEntries, upsertPitEntry } from '../../api';
import { refreshActivePolls } from '../../hooks/useSingleFlightPolling';

const entry = (teamKey: string, payload: Record<string, unknown>, conflicts: PitConflict[] = []) =>
  ({ id: 1, event_key: '2026old', team_key: teamKey, scout_profile: 'Alex', payload, photos: [], conflicts, created_at: null, updated_at: '2026-11-11T10:00:00Z' }) as PitScoutingEntry;
const serverHas = (...entries: PitScoutingEntry[]) => vi.mocked(listPitEntries).mockResolvedValue({ ok: true, entries });
const sent = () => vi.mocked(upsertPitEntry).mock.calls.at(-1)![0];

beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn();
  vi.mocked(listPitEntries).mockReset();
  serverHas();
  // The server's merge, simplified: apply each change's "to".
  vi.mocked(upsertPitEntry).mockImplementation(async (body) => {
    const before = vi.mocked(listPitEntries).mock.results.length ? ((await vi.mocked(listPitEntries).mock.results.at(-1)!.value) as { entries: PitScoutingEntry[] }).entries.find((e) => e.team_key === body.team_key)?.payload ?? {} : {};
    const payload: Record<string, unknown> = { ...before };
    for (const [key, change] of Object.entries(body.changes ?? {})) {
      if ('to' in change) payload[key] = change.to;
      else delete payload[key];
    }
    return { ok: true, entry: entry(body.team_key, payload) };
  });
});
afterEach(() => { cleanup(); localStorage.clear(); vi.clearAllMocks(); });

const renderPage = async () => {
  render(<MemoryRouter initialEntries={['/?event=2026old']}><PitScoutingPage /></MemoryRouter>);
  await screen.findByRole('button', { name: /Team 100/ });
};
const poll = () => act(async () => { await refreshActivePolls(); });
const bar = () => within(screen.getByRole('group', { name: 'Save pit entry' }));

it('saves only what this scout changed, and shows what the merge sent back', async () => {
  serverHas(entry('frc200', { notes: 'done', drivetrain: 'Swerve' }));
  await renderPage();
  fireEvent.click(screen.getByRole('tab', { name: /^Done/ }));
  fireEvent.click(screen.getByRole('button', { name: /^Team 200/ }));
  fireEvent.change(screen.getAllByRole('spinbutton')[0], { target: { value: '5' } });
  fireEvent.click(bar().getByRole('button', { name: 'Save' }));
  await act(async () => {});
  const body = sent();
  expect(body.payload).toMatchObject({ notes: 'done', drivetrain: 'Swerve' });
  expect(Object.values(body.changes!)).toEqual([{ to: 5 }]);
  expect(screen.getByDisplayValue('done')).toBeInTheDocument();
});

it("a teammate's answers fill the fields I haven't touched, and Save sends only mine", async () => {
  await renderPage();
  fireEvent.click(screen.getByRole('button', { name: /Team 100/ }));
  fireEvent.change(screen.getAllByRole('spinbutton')[0], { target: { value: '5' } });
  serverHas(entry('frc100', { notes: 'Alex: swerve, climbs L2' }));
  await poll();
  expect(screen.getByDisplayValue('Alex: swerve, climbs L2')).toBeInTheDocument();
  expect(screen.getAllByRole('spinbutton')[0]).toHaveValue(5);
  expect(bar().getByRole('status')).toHaveTextContent("Alex just saved notes for #100. Their answers are in; yours are still here.");
  fireEvent.click(bar().getByRole('button', { name: 'Save' }));
  await act(async () => {});
  expect(Object.keys(sent().changes!)).toHaveLength(1);
  expect(sent().changes).not.toHaveProperty('notes');
});

it('a draft from earlier picks up what teammates saved since, keeping its own edits', async () => {
  localStorage.setItem('frcmob_pit_draft_v1:1:2026old:frc100', JSON.stringify({ payload: { weight_lbs: 99 }, base: {}, savedAt: 1 }));
  localStorage.setItem('frcmob_pit_selection_v1:1:2026old', 'frc100');
  serverHas(entry('frc100', { notes: 'Alex was here' }));
  render(<MemoryRouter initialEntries={['/?event=2026old']}><PitScoutingPage /></MemoryRouter>);
  await screen.findByRole('heading', { name: 'Team 100' });
  expect(await screen.findByDisplayValue('Alex was here')).toBeInTheDocument();
  expect(screen.getAllByRole('spinbutton')[0]).toHaveValue(99);
  expect(bar().getByRole('status')).toHaveTextContent('Their answers are in; yours are still here.');
});

it('answers that differ are shown with both values and settled with one tap', async () => {
  const conflict = { field: 'drivetrain', kept: 'Tank / KOP', other: 'Swerve', other_by: 'Alex', at: '2026-11-11T10:00:00Z' };
  serverHas(entry('frc200', { drivetrain: 'Tank / KOP' }, [conflict]));
  await renderPage();
  fireEvent.click(screen.getByRole('tab', { name: /^Done/ }));
  expect(screen.getByRole('button', { name: /^Team 200.*1 answer differ/ })).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: /^Team 200/ }));
  const callout = screen.getByRole('status', { name: 'Answers that differ' });
  expect(callout).toHaveTextContent('Drivetrain: saved as Tank / KOP; Alex said Swerve.');
  await act(async () => { fireEvent.click(within(callout).getByRole('button', { name: 'Use Swerve' })); });
  expect(sent().changes).toEqual({ drivetrain: { from: 'Tank / KOP', to: 'Swerve' } });
  expect(screen.queryByRole('status', { name: 'Answers that differ' })).not.toBeInTheDocument();
});

it('keeping the saved answer also settles it, sending it as an explicit choice', async () => {
  const conflict = { field: 'weight_lbs', kept: 120, other: 112, other_by: 'Sam', at: '2026-11-11T10:00:00Z' };
  serverHas(entry('frc200', { weight_lbs: 120 }, [conflict]));
  await renderPage();
  fireEvent.click(screen.getByRole('tab', { name: /^Done/ }));
  fireEvent.click(screen.getByRole('button', { name: /^Team 200/ }));
  const callout = screen.getByRole('status', { name: 'Answers that differ' });
  expect(callout).toHaveTextContent('Weight: saved as 120 lbs; Sam said 112 lbs.');
  await act(async () => { fireEvent.click(within(callout).getByRole('button', { name: 'Keep 120 lbs' })); });
  expect(sent().changes).toEqual({ weight_lbs: { from: 120, to: 120 } });
});

it("a save the server merged with someone else's different answer says so", async () => {
  serverHas(entry('frc200', { drivetrain: 'Swerve' }));
  vi.mocked(upsertPitEntry).mockResolvedValueOnce({ ok: true, entry: entry('frc200', { drivetrain: 'Mecanum' }, [{ field: 'drivetrain', kept: 'Mecanum', other: 'Tank / KOP', other_by: 'Sam', at: 'x' }]) });
  await renderPage();
  fireEvent.click(screen.getByRole('tab', { name: /^Done/ }));
  fireEvent.click(screen.getByRole('button', { name: /^Team 200/ }));
  fireEvent.click(screen.getByRole('radio', { name: 'Mecanum' }));
  await act(async () => { fireEvent.click(bar().getByRole('button', { name: 'Save' })); });
  expect(bar().getByRole('status')).toHaveTextContent('Saved #200. Sam had a different answer for Drivetrain; pick one above.');
  expect(screen.getByRole('status', { name: 'Answers that differ' })).toBeInTheDocument();
});
