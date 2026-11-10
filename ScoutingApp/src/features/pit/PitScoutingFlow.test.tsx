import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

vi.mock('../../components/PageViewBar', () => ({ PageViewBar: () => null }));
vi.mock('../../components/EventPicker', () => ({ EventPicker: () => null }));
vi.mock('../workspace/WorkspaceGate', () => ({ WorkspaceGate: ({ children }: { children: React.ReactNode }) => children }));
const member = vi.hoisted(() => ({ name: 'Priya' }));
vi.mock('../workspace/useWorkspace', () => ({ useWorkspace: () => ({ workspace: { id: 1 }, me: { role: 'member', display_name: member.name } }) }));
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
}));
import { PitScoutingPage } from '../../pages/PitScoutingPage';
import { QueuedForSyncError, upsertPitEntry } from '../../api';

beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn();
  member.name = 'Priya';
  vi.mocked(upsertPitEntry).mockImplementation(async (body) => ({ ok: true, entry: { id: 1, event_key: body.event_key, team_key: body.team_key, scout_profile: null, payload: body.payload, photos: [], created_at: null, updated_at: null } }));
});
afterEach(() => { cleanup(); localStorage.clear(); vi.clearAllMocks(); });

const renderPage = () => render(<MemoryRouter initialEntries={['/?event=2026old']}><PitScoutingPage /></MemoryRouter>);

it('lists unscouted teams first with nicknames, and a check plus screen reader text on done ones', async () => {
  renderPage();
  await screen.findByRole('button', { name: /Team 100/ });
  fireEvent.click(screen.getByLabelText('Unscouted only'));
  const cells = screen.getAllByRole('button', { name: /^Team \d+/ }).map((cell) => cell.getAttribute('aria-label'));
  expect(cells).toEqual(['Team 100, Alpha Bots', 'Team 300, Charlie Crew', 'Team 200, Bravo, completed']);
  expect(screen.getByText('Charlie Crew')).toBeInTheDocument();
});

it('starts on unscouted only and filters by number or nickname', async () => {
  renderPage();
  await screen.findByRole('button', { name: /Team 100/ });
  expect(screen.getByLabelText('Unscouted only')).toBeChecked();
  expect(screen.queryByRole('button', { name: /Team 200/ })).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Find a team'), { target: { value: 'charlie' } });
  expect(screen.getAllByRole('button', { name: /^Team \d+/ })).toHaveLength(1);
  expect(screen.getByRole('button', { name: /Team 300/ })).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Find a team'), { target: { value: '10' } });
  expect(screen.getByRole('button', { name: /Team 100/ })).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Find a team'), { target: { value: 'zzz' } });
  expect(screen.getByText('No team matches "zzz".')).toBeInTheDocument();
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
