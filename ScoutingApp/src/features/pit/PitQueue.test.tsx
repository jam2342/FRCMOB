import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { PitAssignment, PitScoutingEntry } from '../../api';

const session = vi.hoisted(() => ({ role: 'member' as 'member' | 'leader' }));
const online = vi.hoisted(() => ({ value: true }));
vi.mock('../workspace/useWorkspace', () => ({
  useWorkspace: () => ({ workspace: { id: 3 }, me: { id: 7, role: session.role, display_name: 'Jamal' } }),
}));
vi.mock('../../hooks/useOnlineStatus', () => ({ useOnlineStatus: () => ({ online: online.value }) }));
vi.mock('../../utils/offlineQueue', () => ({ listQueuedMutations: vi.fn(async () => []) }));
vi.mock('../../api', () => ({
  getPitAssignments: vi.fn(),
  claimPitTeam: vi.fn(),
  releasePitTeam: vi.fn(),
  setPitAssignment: vi.fn(),
  autoSplitPitTeams: vi.fn(),
  getMyWorkspace: vi.fn(),
}));
import { autoSplitPitTeams, claimPitTeam, getMyWorkspace, getPitAssignments, releasePitTeam, setPitAssignment } from '../../api';
import { listQueuedMutations } from '../../utils/offlineQueue';
import { PitTeamPicker } from './PitTeamPicker';

const team = (n: number, nickname: string) => ({ team_key: `frc${n}`, team_number: n, nickname });
const teams = [team(100, 'Alpha'), team(200, 'Bravo'), team(300, 'Charlie'), team(400, 'Delta'), team(500, 'Echo')];
const entry = (updated: string | null, photos: string[] = []) =>
  ({ payload: { notes: 'x' }, photos, updated_at: updated }) as unknown as PitScoutingEntry;
const row = (teamKey: string, id: number, name: string): PitAssignment => ({
  team_key: teamKey, member_id: id, member_name: name, source: 'claim', assigned_by_member_id: id, updated_at: null,
});

async function renderPicker(props: Partial<React.ComponentProps<typeof PitTeamPicker>> = {}) {
  const onSelect = vi.fn();
  render(
    <PitTeamPicker
      eventKey="2026arc"
      teams={teams}
      entriesByTeam={new Map([['frc500', entry(new Date(Date.now() - 12 * 60_000).toISOString(), ['a.jpg'])]])}
      selectedTeam=""
      formDirty={false}
      disabled={false}
      collapsible={false}
      onSelect={onSelect}
      {...props}
    />,
  );
  // Let the assignments and the offline queue answer before anything is asserted.
  await act(async () => {});
  await act(async () => {});
  return onSelect;
}

const teamRows = () => screen.getAllByRole('button', { name: /^Team \d+/ }).map((button) => button.getAttribute('aria-label'));

beforeEach(() => {
  session.role = 'member';
  online.value = true;
  localStorage.clear();
  vi.mocked(listQueuedMutations).mockResolvedValue([]);
  vi.mocked(getPitAssignments).mockResolvedValue([row('frc200', 8, 'Alex'), row('frc300', 7, 'Jamal')]);
  vi.mocked(getMyWorkspace).mockResolvedValue({
    members: [
      { id: 7, display_name: 'Jamal', role: 'leader' },
      { id: 8, display_name: 'Alex', role: 'member' },
      { id: 9, display_name: 'Sam', role: 'member' },
    ],
  } as never);
});
afterEach(() => { cleanup(); vi.clearAllMocks(); });

it('opens on Mine when something is assigned to me, and keeps the tab I choose', async () => {
  await renderPicker();
  expect(await screen.findByRole('tab', { name: 'Mine, 1' })).toHaveAttribute('aria-selected', 'true');
  expect(teamRows()).toEqual(['Team 300, Charlie, assigned to you, to do']);
  fireEvent.click(screen.getByRole('tab', { name: 'Left, 4' }));
  expect(teamRows()).toEqual([
    'Team 100, Alpha, not assigned, to do',
    'Team 200, Bravo, assigned to Alex, to do',
    'Team 300, Charlie, assigned to you, to do',
    'Team 400, Delta, not assigned, to do',
  ]);
});

it('shows a finished team in Done with its camera marker and when it was updated', async () => {
  await renderPicker();
  fireEvent.click(await screen.findByRole('tab', { name: 'Done, 1' }));
  expect(teamRows()).toEqual(['Team 500, Echo, not assigned, done, has photos']);
  expect(screen.getByText('12 min ago')).toBeInTheDocument();
});

it('marks a draft on this phone and a save waiting to sync', async () => {
  localStorage.setItem('frcmob_pit_draft_v1:3:2026arc:frc100', JSON.stringify({ payload: { a: 1 }, savedAt: 1 }));
  vi.mocked(listQueuedMutations).mockResolvedValue([
    { id: '1', queuedAt: '', method: 'POST', url: 'https://x/pit-scouting', body: JSON.stringify({ event_key: '2026arc', team_key: 'frc400' }), headers: {}, attempts: 0 },
    { id: '2', queuedAt: '', method: 'POST', url: 'https://x/pit-scouting', body: JSON.stringify({ event_key: '2025other', team_key: 'frc200' }), headers: {}, attempts: 0 },
  ]);
  await renderPicker();
  fireEvent.click(await screen.findByRole('tab', { name: /Left/ }));
  await act(async () => {});
  expect(screen.getByRole('button', { name: /^Team 100.*draft on this phone/ })).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /^Team 400/ })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('tab', { name: /Done/ }));
  expect(screen.getByRole('button', { name: /^Team 400.*waiting to sync/ })).toBeInTheDocument();
});

it('rows open the team form', async () => {
  const onSelect = await renderPicker();
  fireEvent.click(await screen.findByRole('button', { name: /^Team 300/ }));
  expect(onSelect).toHaveBeenCalledWith('frc300');
  expect(claimPitTeam).not.toHaveBeenCalled();
});

it('I\'m on it shows the team as mine at once and calls the server once', async () => {
  vi.mocked(claimPitTeam).mockResolvedValue({ ok: true, assignment: row('frc100', 7, 'Jamal') });
  await renderPicker();
  fireEvent.click(await screen.findByRole('tab', { name: /Left/ }));
  fireEvent.click(screen.getByRole('button', { name: "I'm on it for 100" }));
  expect(screen.getByRole('button', { name: /^Team 100, Alpha, assigned to you/ })).toBeInTheDocument();
  await act(async () => {});
  expect(claimPitTeam).toHaveBeenCalledTimes(1);
  expect(claimPitTeam).toHaveBeenCalledWith('2026arc', 'frc100', undefined);
});

it('a 409 puts the team back and says who has it in a quiet notice', async () => {
  vi.mocked(claimPitTeam).mockRejectedValue(new Error('frc100 is already taken by Alex.'));
  await renderPicker();
  fireEvent.click(await screen.findByRole('tab', { name: /Left/ }));
  fireEvent.click(screen.getByRole('button', { name: "I'm on it for 100" }));
  await act(async () => {});
  expect(screen.getByText('frc100 is already taken by Alex.')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: /^Team 100, Alpha, not assigned/ })).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }));
  expect(screen.queryByText('frc100 is already taken by Alex.')).not.toBeInTheDocument();
});

it('releases my own team, and offers no claim on a teammate\'s', async () => {
  vi.mocked(releasePitTeam).mockResolvedValue({ ok: true, released: true });
  await renderPicker();
  fireEvent.click(await screen.findByRole('tab', { name: /Left/ }));
  expect(screen.queryByRole('button', { name: "I'm on it for 200" })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Release 200' })).not.toBeInTheDocument();
  vi.mocked(getPitAssignments).mockResolvedValue([row('frc200', 8, 'Alex')]);
  fireEvent.click(screen.getByRole('button', { name: 'Release 300' }));
  await act(async () => {});
  expect(releasePitTeam).toHaveBeenCalledWith('2026arc', 'frc300');
  expect(screen.getByRole('button', { name: /^Team 300, Charlie, not assigned/ })).toBeInTheDocument();
});

it('offline: shows the last assignments with claim and release disabled and a reason', async () => {
  await renderPicker();
  fireEvent.click(await screen.findByRole('tab', { name: /Left/ }));
  online.value = false;
  cleanup();
  await renderPicker();
  fireEvent.click(screen.getByRole('tab', { name: /Left/ }));
  expect(screen.getByRole('status')).toHaveTextContent(/Offline/);
  expect(screen.getByRole('button', { name: "I'm on it for 100" })).toBeDisabled();
  expect(screen.getByRole('button', { name: "I'm on it for 100" })).toHaveAttribute('title', expect.stringMatching(/Offline/));
  expect(claimPitTeam).not.toHaveBeenCalled();
});

it('members get no leader controls', async () => {
  await renderPicker();
  await screen.findByRole('tab', { name: /Mine/ });
  expect(screen.queryByRole('button', { name: 'Auto-split' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /Change who has/ })).not.toBeInTheDocument();
});

it('leader: Auto-split confirms with a summary, then sends only unscouted teams to the chosen scouts', async () => {
  session.role = 'leader';
  const split = [row('frc100', 8, 'Alex'), row('frc400', 9, 'Sam'), row('frc200', 8, 'Alex'), row('frc300', 7, 'Jamal')];
  vi.mocked(autoSplitPitTeams).mockResolvedValue(split);
  await renderPicker();
  fireEvent.click(await screen.findByRole('button', { name: 'Auto-split' }));
  const dialog = await screen.findByRole('dialog', { name: 'Auto-split teams' });
  // 100 and 400 are the to-do teams nobody holds; 200 and 300 are taken, 500 is scouted.
  expect(await within(dialog).findByText('Split 2 teams across 3 scouts?')).toBeInTheDocument();
  fireEvent.click(within(dialog).getByLabelText('Jamal (you)'));
  expect(within(dialog).getByText('Split 2 teams across 2 scouts?')).toBeInTheDocument();
  vi.mocked(getPitAssignments).mockResolvedValue(split);
  await act(async () => { fireEvent.click(within(dialog).getByRole('button', { name: 'Split' })); });
  expect(autoSplitPitTeams).toHaveBeenCalledWith('2026arc', {
    teamKeys: ['frc100', 'frc200', 'frc300', 'frc400'],
    memberIds: [8, 9],
    onlyUnscouted: true,
  });
  expect(within(dialog).getByText('Split 2 teams across 2 scouts.')).toBeInTheDocument();
  expect(within(dialog).getByText('Sam')).toBeInTheDocument();
});

it('leader: reassigns a single team, clears it, and force-takes behind a confirm', async () => {
  session.role = 'leader';
  vi.mocked(setPitAssignment).mockResolvedValue({ ok: true, assignment: row('frc100', 9, 'Sam') });
  vi.mocked(claimPitTeam).mockResolvedValue({ ok: true, assignment: row('frc200', 7, 'Jamal') });
  await renderPicker();
  fireEvent.click(await screen.findByRole('tab', { name: /Left/ }));

  fireEvent.click(screen.getByRole('button', { name: 'Change who has 100' }));
  let dialog = await screen.findByRole('dialog', { name: 'Team 100' });
  fireEvent.change(await within(dialog).findByLabelText('Assigned to'), { target: { value: '9' } });
  await act(async () => { fireEvent.click(within(dialog).getByRole('button', { name: 'Save' })); });
  expect(setPitAssignment).toHaveBeenCalledWith('2026arc', 'frc100', 9);

  fireEvent.click(screen.getByRole('button', { name: 'Change who has 200' }));
  dialog = await screen.findByRole('dialog', { name: 'Team 200' });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Take it from Alex' }));
  expect(claimPitTeam).not.toHaveBeenCalled();
  dialog = screen.getByRole('dialog', { name: 'Take Team 200?' });
  await act(async () => { fireEvent.click(within(dialog).getByRole('button', { name: 'Take it' })); });
  expect(claimPitTeam).toHaveBeenCalledWith('2026arc', 'frc200', { force: true });
});

it('on a phone the open team shows who has it and claim sits beside Change team', async () => {
  vi.mocked(claimPitTeam).mockResolvedValue({ ok: true, assignment: row('frc100', 7, 'Jamal') });
  vi.mocked(getPitAssignments).mockResolvedValue([]);
  await renderPicker({ collapsible: true, selectedTeam: 'frc100' });
  fireEvent.click(await screen.findByRole('button', { name: "I'm on it" }));
  vi.mocked(getPitAssignments).mockResolvedValue([row('frc100', 7, 'Jamal')]);
  await act(async () => {});
  expect(claimPitTeam).toHaveBeenCalledWith('2026arc', 'frc100', undefined);
  expect(screen.getByText("You're on it")).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Release' })).toBeInTheDocument();
});
