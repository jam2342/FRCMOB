import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

vi.mock('../../components/PageViewBar', () => ({ PageViewBar: () => null }));
vi.mock('../../components/EventPicker', () => ({ EventPicker: () => null }));
vi.mock('../workspace/WorkspaceGate', () => ({ WorkspaceGate: ({ children }: { children: React.ReactNode }) => children }));
const member = vi.hoisted(() => ({ name: 'Priya' }));
vi.mock('../workspace/useWorkspace', () => ({ useWorkspace: () => ({ workspace: { id: 1 }, me: { id: 7, role: 'member', display_name: member.name } }) }));
vi.mock('../../hooks/useOnlineStatus', () => ({ useOnlineStatus: () => ({ online: true }) }));
vi.mock('../../utils/offlineQueue', () => ({ listQueuedMutations: vi.fn(async () => []) }));
vi.mock('../../api', async original => ({
  ...(await original<typeof import('../../api')>()),
  getEventTeamsIntel: vi.fn(async () => ({ teams: [
    { team_key: 'frc100', team_number: 100, nickname: 'Alpha Bots' },
    { team_key: 'frc200', team_number: 200, nickname: 'Bravo' },
    { team_key: 'frc300', team_number: 300, nickname: 'Charlie Crew' },
  ] })),
  listPitEntries: vi.fn(async () => ({ entries: [{ team_key: 'frc200', payload: { notes: 'done' }, photos: [], updated_at: null }] })),
  upsertPitEntry: vi.fn(),
  getPitAssignments: vi.fn(async () => []),
  claimPitTeam: vi.fn(),
}));
import { PitScoutingPage } from '../../pages/PitScoutingPage';
import { QueuedForSyncError, upsertPitEntry } from '../../api';

beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn();
  member.name = 'Priya';
  vi.mocked(upsertPitEntry).mockImplementation(async (body) => ({ ok: true, entry: { id: 1, event_key: body.event_key, team_key: body.team_key, scout_profile: null, payload: body.payload, photos: [], created_at: null, updated_at: null } }));
});
afterEach(() => { cleanup(); localStorage.clear(); vi.clearAllMocks(); });

const renderPage = (search = '?event=2026old') => render(<MemoryRouter initialEntries={['/' + search]}><PitScoutingPage /></MemoryRouter>);

it('lists the teams still to do as queue rows, with who has them and a screen reader sentence', async () => {
  renderPage();
  await screen.findByRole('button', { name: /Team 100/ });
  const rows = screen.getAllByRole('button', { name: /^Team \d+/ }).map((row) => row.getAttribute('aria-label'));
  expect(rows).toEqual(['Team 100, Alpha Bots, not assigned, to do', 'Team 300, Charlie Crew, not assigned, to do']);
  expect(screen.getByText('Charlie Crew')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('tab', { name: 'Done, 1' }));
  expect(screen.getAllByRole('button', { name: /^Team \d+/ }).map((row) => row.getAttribute('aria-label'))).toEqual(['Team 200, Bravo, not assigned, done']);
});

it('opens on Left when nothing is assigned to me, with counts on the tabs, and filters by number or nickname', async () => {
  renderPage();
  await screen.findByRole('button', { name: /Team 100/ });
  expect(screen.getByRole('tab', { name: 'Left, 2' })).toHaveAttribute('aria-selected', 'true');
  expect(screen.getByRole('tab', { name: 'Mine, 0' })).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /Team 200/ })).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Find a team'), { target: { value: 'charlie' } });
  expect(screen.getAllByRole('button', { name: /^Team \d+/ })).toHaveLength(1);
  expect(screen.getByRole('button', { name: /Team 300/ })).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Find a team'), { target: { value: '10' } });
  expect(screen.getByRole('button', { name: /Team 100/ })).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Find a team'), { target: { value: 'zzz' } });
  expect(screen.getByText('No team matches "zzz" here.')).toBeInTheDocument();
});

it('shows the save result in the pinned bar and credits the signed-in member', async () => {
  renderPage();
  fireEvent.click(await screen.findByRole('button', { name: /Team 100/ }));
  const bar = screen.getByRole('group', { name: 'Save pit entry' });
  expect(within(bar).getByRole('status')).toHaveTextContent('Not saved yet.');
  fireEvent.change(screen.getAllByRole('spinbutton')[0], { target: { value: '5' } });
  expect(within(bar).getByRole('status')).toHaveTextContent('Draft kept on this phone');
  localStorage.setItem('scouting_manual_profile_v1', 'Whoever Was Here Before');
  fireEvent.click(within(bar).getByRole('button', { name: 'Save' }));
  await act(async () => {});
  expect(within(bar).getByRole('status')).toHaveTextContent('Saved pit entry for #100.');
  expect(vi.mocked(upsertPitEntry).mock.calls[0][0].scout_profile).toBe('Priya');
});

it('falls back to the manual scout profile when the member has no display name', async () => {
  member.name = '';
  localStorage.setItem('scouting_manual_profile_v1', 'Manual Name');
  renderPage();
  fireEvent.click(await screen.findByRole('button', { name: /Team 100/ }));
  fireEvent.click(screen.getByRole('button', { name: 'Save' }));
  await act(async () => {});
  expect(vi.mocked(upsertPitEntry).mock.calls[0][0].scout_profile).toBe('Manual Name');
});

it('Save & next saves, then opens the next unscouted team and says what happened', async () => {
  renderPage();
  fireEvent.click(await screen.findByRole('button', { name: /Team 100/ }));
  fireEvent.change(screen.getAllByRole('spinbutton')[0], { target: { value: '5' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save & next' }));
  await act(async () => {});
  expect(vi.mocked(upsertPitEntry).mock.calls[0][0]).toMatchObject({ team_key: 'frc100' });
  // Team 200 already has notes, so 300 is next.
  expect(screen.getByRole('heading', { name: 'Team 300' })).toBeInTheDocument();
  expect(screen.getByRole('status')).toHaveTextContent('Saved #100. Now on #300.');
});

it('Save & next stays put and reports the error when the save fails', async () => {
  vi.mocked(upsertPitEntry).mockRejectedValue(new Error('server said no'));
  renderPage();
  fireEvent.click(await screen.findByRole('button', { name: /Team 100/ }));
  fireEvent.click(screen.getByRole('button', { name: 'Save & next' }));
  await act(async () => {});
  expect(screen.getByRole('heading', { name: 'Team 100' })).toBeInTheDocument();
  expect(screen.getByRole('alert')).toHaveTextContent('server said no');
});

it('Save & next on a queued save still moves on and says it will sync', async () => {
  vi.mocked(upsertPitEntry).mockRejectedValue(new QueuedForSyncError('queued'));
  renderPage();
  fireEvent.click(await screen.findByRole('button', { name: /Team 100/ }));
  fireEvent.click(screen.getByRole('button', { name: 'Save & next' }));
  await act(async () => {});
  expect(screen.getByRole('heading', { name: 'Team 300' })).toBeInTheDocument();
  expect(screen.getByRole('status')).toHaveTextContent('Saved #100 on this phone, will sync. Now on #300.');
});

it('answers yes and no with toggle buttons that clear on a second tap', async () => {
  renderPage();
  fireEvent.click(await screen.findByRole('button', { name: /Team 100/ }));
  const group = screen.getAllByRole('group').find((item) => within(item).queryAllByRole('radio').length === 2)!;
  const yes = within(group).getByRole('radio', { name: 'Yes' });
  fireEvent.click(yes);
  expect(yes).toBeChecked();
  fireEvent.click(within(group).getByRole('radio', { name: 'No' }));
  expect(yes).not.toBeChecked();
  fireEvent.click(within(group).getByRole('radio', { name: 'No' }));
  expect(within(group).getByRole('radio', { name: 'No' })).not.toBeChecked();
});

it('a ?team= link opens that team without claiming it, and ignores bad or unknown keys', async () => {
  const { claimPitTeam } = await import('../../api');
  renderPage('?event=2026old&team=frc300');
  expect(await screen.findByRole('heading', { name: 'Team 300' })).toBeInTheDocument();
  expect(claimPitTeam).not.toHaveBeenCalled();
  cleanup();
  localStorage.clear();
  renderPage('?event=2026old&team=frc999');
  await screen.findByRole('button', { name: /^Team 100/ });
  expect(screen.queryByRole('heading', { name: /^Team 999/ })).not.toBeInTheDocument();
  cleanup();
  renderPage('?event=2026old&team=300%3Cscript%3E');
  await screen.findByRole('button', { name: /^Team 100/ });
  expect(screen.queryByRole('heading', { name: 'Team 300' })).not.toBeInTheDocument();
});
