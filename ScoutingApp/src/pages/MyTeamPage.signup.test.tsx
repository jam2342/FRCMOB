import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createWorkspace, getWorkspaceSignupPolicy, joinWorkspace } from '../api';
import { MyTeamPage } from './MyTeamPage';
import { signOutTestWorkspace } from '../test/workspace';
import { isNativeApp } from '../platform/runtime';

vi.mock('../api', () => ({
  // The thank-you note has its own tests; here nobody has scouted yet.
  getScoutingCoverage: vi.fn(async () => ({ ok: true, event_key: '', summary: { total_slots: 0, covered_slots: 0, coverage_pct: 0, total_entries: 0, scout_count: 0, outlier_count: 0 }, grid: [], leaderboard: [], outliers: [] })),
  // The season card has its own tests; here the team hasn't played yet.
  getTeamSeasonStory: vi.fn(async () => ({ ok: true, team_key: '', nickname: null, season: 2026, game: null, events: [], totals: { wins: 0, losses: 0, ties: 0, events: 0, event_wins: 0, best_match: null } })),
  getWorkspaceSignupPolicy: vi.fn(),
  createWorkspace: vi.fn(),
  joinWorkspace: vi.fn(),
}));
vi.mock('../components/ui/OfflineReadyCard', () => ({ OfflineReadyCard: () => null }));
vi.mock('../features/offline/SyncStatusCard', () => ({ SyncStatusCard: () => null }));
vi.mock('../platform/runtime', async (original) => ({
  ...await original<typeof import('../platform/runtime')>(),
  isNativeApp: vi.fn(() => false),
}));

type Options = Parameters<NonNullable<Window['turnstile']>['render']>[1];
let options: Options;
const renderWidget = vi.fn((_container: HTMLElement, settings: Options) => { options = settings; return 'widget'; });
const removeWidget = vi.fn();
const createButton = () => screen.getByRole('button', { name: 'Create workspace' });
function page() { return render(<MemoryRouter><MyTeamPage /></MemoryRouter>); }
function fillCreate() {
  fireEvent.change(screen.getByLabelText(/Workspace name/), { target: { value: 'Eagle Robotics' } });
  fireEvent.change(screen.getAllByLabelText(/Your name/)[1], { target: { value: 'Ann' } });
}

beforeEach(() => {
  vi.resetAllMocks();
  signOutTestWorkspace();
  vi.mocked(isNativeApp).mockReturnValue(false);
  vi.mocked(getWorkspaceSignupPolicy).mockResolvedValue({ required: true, site_key: 'public-key' });
  renderWidget.mockImplementation((_container, settings) => { options = settings; return 'widget'; });
  window.turnstile = { render: renderWidget, remove: removeWidget };
});
afterEach(() => { delete window.turnstile; });

describe('workspace signup policy', () => {
  it('waits for policy, requires a token, submits it and discards it after a failed request', async () => {
    vi.mocked(createWorkspace).mockRejectedValue(new Error('Server could not create your workspace.'));
    page();
    expect(createButton()).toBeDisabled();
    fillCreate();
    await waitFor(() => expect(renderWidget).toHaveBeenCalledOnce());
    expect(createButton()).toBeDisabled();
    act(() => options.callback('single-use'));
    expect(createButton()).toBeEnabled();
    fireEvent.click(createButton());
    await screen.findByRole('alert');
    expect(createWorkspace).toHaveBeenCalledWith({ name: 'Eagle Robotics', display_name: 'Ann', frc_team_number: null, challenge_token: 'single-use' });
    expect(createButton()).toBeDisabled();
    await waitFor(() => expect(renderWidget).toHaveBeenCalledTimes(2));
    expect(removeWidget).toHaveBeenCalledWith('widget');
    act(() => options.callback('replacement'));
    expect(createButton()).toBeEnabled();
    act(() => options['expired-callback']());
    expect(createButton()).toBeDisabled();
  });

  it('permits local creation when the server explicitly does not require a challenge', async () => {
    vi.mocked(getWorkspaceSignupPolicy).mockResolvedValue({ required: false, site_key: null });
    page();
    fillCreate();
    await waitFor(() => expect(createButton()).toBeEnabled());
    fireEvent.click(createButton());
    await waitFor(() => expect(createWorkspace).toHaveBeenCalledWith({ name: 'Eagle Robotics', display_name: 'Ann', frc_team_number: null, challenge_token: null }));
    expect(renderWidget).not.toHaveBeenCalled();
  });

  it.each(['missing-key', 'policy-error'])('blocks creation for %s and still allows joining', async (failure) => {
    if (failure === 'missing-key') vi.mocked(getWorkspaceSignupPolicy).mockResolvedValue({ required: true, site_key: null });
    else vi.mocked(getWorkspaceSignupPolicy).mockRejectedValue(new Error('Offline'));
    page();
    await screen.findByText(/Workspace creation is temporarily unavailable/);
    expect(createButton()).toBeDisabled();
    expect(renderWidget).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText(/Join code/), { target: { value: 'ABCDE12345' } });
    fireEvent.change(screen.getAllByLabelText(/Your name/)[0], { target: { value: 'Sam' } });
    fireEvent.click(screen.getByRole('button', { name: 'Join team' }));
    await waitFor(() => expect(joinWorkspace).toHaveBeenCalledWith({ join_code: 'ABCDE12345', display_name: 'Sam' }));
    expect(createWorkspace).not.toHaveBeenCalled();
  });

  it('offers a browser signup path for native apps without bypassing the challenge', async () => {
    vi.mocked(isNativeApp).mockReturnValue(true);
    page();
    const link = await screen.findByRole('link', { name: 'Open My Team in a browser' });
    expect(new URL(link.getAttribute('href')!).hash).toBe('#/my-team');
    expect(createButton()).toBeDisabled();
    expect(renderWidget).not.toHaveBeenCalled();
  });
});
