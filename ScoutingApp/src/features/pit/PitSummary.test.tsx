import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { PIT_FORM_SECTIONS } from '../../config/gameFields';

const workspaceState = vi.hoisted(() => ({ value: { workspace: { id: 3 } } as unknown }));
vi.mock('../workspace/useWorkspace', () => ({ useWorkspace: () => workspaceState.value }));
vi.mock('../../api', async (original) => ({
  ...(await original<typeof import('../../api')>()),
  listPitEntries: vi.fn(),
}));

import { listPitEntries } from '../../api';
import { PitSummary } from './PitSummary';
import { resetPitEntriesCache } from './usePitEntries';

const entry = {
  id: 1,
  event_key: '2026arc',
  team_key: 'frc254',
  scout_profile: 'Dana',
  payload: {
    drivetrain: 'Swerve',
    weight_lbs: 118,
    vision_system: true,
    can_cross_bump: false,
    claimed_climb: 'Level 3',
    claimed_cycle_time_sec: 9,
    auto_capabilities: ['Mobility', 'Scores preload'],
    notes: '',
  },
  photos: ['a.jpg', 'b.jpg'],
  photo_urls: ['/media/a.jpg', '/media/b.jpg'],
  created_at: null,
  updated_at: new Date(Date.now() - 2 * 3600_000).toISOString(),
};

function mount(teamKey: string, variant?: 'full' | 'compact') {
  return render(
    <MemoryRouter>
      <PitSummary teamKey={teamKey} eventKey="2026arc" variant={variant} />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  resetPitEntriesCache();
  workspaceState.value = { workspace: { id: 3 } };
  vi.mocked(listPitEntries).mockResolvedValue({ ok: true, entries: [entry] } as never);
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

it('labels answers from the form config and groups them by its sections', async () => {
  const { container } = mount('frc254');
  await screen.findByText('Swerve');
  const labels = PIT_FORM_SECTIONS.flatMap((section) => section.fields).reduce<Record<string, string>>((all, field) => ({ ...all, [field.key]: field.label }), {});
  expect(screen.getByText(labels.drivetrain)).toBeTruthy();
  expect(screen.getByText(labels.claimed_climb)).toBeTruthy();
  expect(screen.getByRole('heading', { name: 'Robot' })).toBeTruthy();
  expect(screen.getByRole('heading', { name: 'Claimed Capabilities' })).toBeTruthy();
  // Strategy only had an empty note, so its section is gone.
  expect(screen.queryByRole('heading', { name: 'Strategy' })).toBeNull();
  expect(container.textContent).toContain('Scouted by Dana · 2h ago');
});

it('omits unanswered questions and writes yes/no and units as text', async () => {
  mount('frc254');
  await screen.findByText('Swerve');
  expect(screen.queryByText('Language')).toBeNull();
  expect(screen.queryByText('Notes')).toBeNull();
  expect(screen.getByText('118 lbs')).toBeTruthy();
  expect(screen.getByText('9 sec')).toBeTruthy();
  expect(screen.getByText('Yes')).toBeTruthy();
  // A toggle answered "no" is an answer.
  expect(screen.getByText('No')).toBeTruthy();
  expect(screen.getByText('Mobility, Scores preload')).toBeTruthy();
});

it('opens a photo larger from the strip', async () => {
  mount('frc254');
  fireEvent.click(await screen.findByRole('button', { name: 'Open photo 2 of 2' }));
  const dialog = screen.getByRole('dialog');
  expect(dialog.querySelector('img')?.getAttribute('src')).toContain('b.jpg');
  fireEvent.keyDown(window, { key: 'Escape' });
  expect(screen.queryByRole('dialog')).toBeNull();
});

it('shows only the decision answers, a photo count and a link in the compact variant', async () => {
  mount('frc254', 'compact');
  await screen.findByText('Swerve');
  expect(screen.getByText('Level 3')).toBeTruthy();
  expect(screen.getByText('9 sec')).toBeTruthy();
  expect(screen.queryByText('118 lbs')).toBeNull();
  expect(screen.queryByRole('heading')).toBeNull();
  expect(screen.getByText(/2 photos/)).toBeTruthy();
  expect(screen.getByRole('link', { name: 'Pit notes →' }).getAttribute('href')).toBe('/team-center?team=frc254&event=2026arc&tab=media');
});

it('says so, with a way to scout the team, when a workspace has no entry for it', async () => {
  mount('frc1678');
  expect(await screen.findByText('Not pit scouted yet')).toBeTruthy();
  expect(screen.getByRole('link', { name: 'Pit scout →' }).getAttribute('href')).toBe('/scouting/pit?event=2026arc&team=frc1678');
});

it('renders nothing, and asks for nothing, outside a workspace', async () => {
  workspaceState.value = null;
  const { container } = mount('frc254');
  await new Promise((resolve) => setTimeout(resolve, 20));
  expect(container.innerHTML).toBe('');
  expect(listPitEntries).not.toHaveBeenCalled();
});

it('shares one request between summaries of the same event', async () => {
  render(
    <MemoryRouter>
      <PitSummary teamKey="frc254" eventKey="2026arc" variant="compact" />
      <PitSummary teamKey="frc1678" eventKey="2026arc" variant="compact" />
    </MemoryRouter>,
  );
  await waitFor(() => expect(screen.getByText('Not pit scouted yet')).toBeTruthy());
  expect(listPitEntries).toHaveBeenCalledTimes(1);
});
