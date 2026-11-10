import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ensureTeamRoom, getMyWorkspace, getScoutingCoverage, getTeamSeasonStory, joinWorkspace } from '../api';
import { MyTeamPage } from './MyTeamPage';
import { pitProgress } from '../features/workspace/pitProgress';
import { clearWorkspaceSession, rememberIssuedJoinCode } from '../features/workspace/workspaceSession';
import { signInTestWorkspace, signOutTestWorkspace } from '../test/workspace';
import { copyTextToClipboard } from './centerUtils';

const members = [
  { id: 1, display_name: 'Test Scout', role: 'leader', joined_at: null, last_seen_at: null },
  { id: 2, display_name: 'Sam', role: 'member', joined_at: null, last_seen_at: null },
];
const workspace = { id: 1, name: 'Eagle Robotics', frc_team_number: 5417, join_code_rotated_at: null, created_at: null };

vi.mock('../api', () => ({
  getScoutingCoverage: vi.fn(async () => ({ ok: true, event_key: '', summary: { total_slots: 0, covered_slots: 0, coverage_pct: 0, total_entries: 0, scout_count: 0, outlier_count: 0 }, grid: [], leaderboard: [], outliers: [] })),
  getTeamSeasonStory: vi.fn(async () => ({ ok: true, team_key: '', nickname: null, season: 2026, game: null, events: [], totals: { wins: 0, losses: 0, ties: 0, events: 0, event_wins: 0, best_match: null } })),
  getWorkspaceSignupPolicy: vi.fn(async () => ({ required: false, site_key: null })),
  getMyWorkspace: vi.fn(),
  getEventTeamsIntel: vi.fn(async () => ({ teams: [{ team_key: 'frc254' }, { team_key: 'frc118' }, { team_key: 'frc1678' }] })),
  listPitEntries: vi.fn(async () => ({ ok: true, entries: [{ team_key: 'frc254' }, { team_key: 'frc9999' }] })),
  ensureTeamRoom: vi.fn(async () => null),
  getTeamRoom: vi.fn(async () => null),
  getEventSchedule: vi.fn(async () => ({ ok: true, event_key: '2026arc', event_name: 'Archimedes', matches: [] })),
  joinWorkspace: vi.fn(),
  createWorkspace: vi.fn(),
  leaveWorkspace: vi.fn(),
  removeWorkspaceMember: vi.fn(),
  renameMeInWorkspace: vi.fn(),
  rotateWorkspaceJoinCode: vi.fn(),
  setWorkspaceMemberRole: vi.fn(),
  updateMyWorkspace: vi.fn(),
}));
vi.mock('../hooks/useOnlineStatus', () => ({
  useOnlineStatus: () => ({ online: true, queueSize: 0, isShowingOfflineData: false }),
}));
vi.mock('../components/ui/OfflineReadyCard', () => ({ OfflineReadyCard: () => null }));
vi.mock('../features/offline/SyncStatusCard', () => ({ SyncStatusCard: () => null }));
vi.mock('./centerUtils', async (original) => ({
  ...await original<typeof import('./centerUtils')>(),
  copyTextToClipboard: vi.fn(async () => true),
}));
vi.mock('qrcode', () => ({ default: { toCanvas: vi.fn(async () => undefined) } }));

function renderAt(path = '/my-team') {
  return render(<MemoryRouter initialEntries={[path]}><MyTeamPage /></MemoryRouter>);
}
function mockRole(role: 'leader' | 'member') {
  vi.mocked(getMyWorkspace).mockResolvedValue({ ok: true, workspace, me: members[role === 'leader' ? 0 : 1], members } as never);
}
const headings = () => screen.getAllByRole('heading').map((h) => h.textContent ?? '');

describe('My Team hub', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    localStorage.setItem('scouting_center_event_key', '2026arc');
    signOutTestWorkspace();
    vi.mocked(getTeamSeasonStory).mockResolvedValue({ ok: true, team_key: '', nickname: null, season: 2026, game: null, events: [], totals: { wins: 0, losses: 0, ties: 0, events: 0, event_wins: 0, best_match: null } });
    vi.mocked(getScoutingCoverage).mockResolvedValue({ ok: true, event_key: '', summary: { total_slots: 0, covered_slots: 0, coverage_pct: 0, total_entries: 0, scout_count: 0, outlier_count: 0 }, grid: [], leaderboard: [], outliers: [] });
  });
  afterEach(() => {
    clearWorkspaceSession('left');
    Reflect.deleteProperty(navigator, 'share');
  });

  it('counts only entries for teams at the event', () => {
    expect(pitProgress(['frc254', 'FRC118'], ['frc254', 'frc254', 'frc9999'])).toEqual({ done: 1, total: 2 });
  });

  it('links to pit scouting with progress, coverage and the picklist', async () => {
    signInTestWorkspace('member');
    mockRole('member');
    renderAt();
    const pit = await screen.findByRole('link', { name: /Pit scouting/ });
    await waitFor(() => expect(pit.textContent).toContain('1/3 teams'));
    expect(pit.getAttribute('href')).toBe('/scouting/pit?event=2026arc');
    expect(screen.getByRole('link', { name: /Coverage/ }).getAttribute('href')).toBe('/scouting/coverage?event=2026arc');
    expect(screen.getByRole('link', { name: /Picklist/ }).getAttribute('href')).toBe('/compare/picklist?event=2026arc');
  });

  it("puts a leader's tools before the season recap", async () => {
    signInTestWorkspace('leader');
    mockRole('leader');
    // The recap hides itself for a team with no events, so give this one a season.
    vi.mocked(getTeamSeasonStory).mockResolvedValue({
      ok: true, team_key: 'frc5417', nickname: null, season: 2026, game: null,
      events: [{ event_key: '2026arc', event_name: 'Archimedes', start: null, qual_rank: 3, field_size: 75, record: { wins: 5, losses: 3, ties: 0 }, best_match: null, finish: 'quals', rating_0_100: 50, points_per_match: 40 }],
      totals: { wins: 5, losses: 3, ties: 0, events: 1, event_wins: 0, best_match: null },
    } as never);
    renderAt();
    // The workspace card is titled from the cached session until the server's copy lands.
    await screen.findByRole('heading', { name: 'Eagle Robotics' });
    await screen.findByRole('heading', { name: 'Invite scouts' });
    const at = (order: string[], text: RegExp) => order.findIndex((h) => text.test(h));
    // The season recap loads on its own clock, so wait for it to be in the list.
    await waitFor(() => expect(at(headings(), /Our season/)).toBeGreaterThan(-1));
    const order = headings();
    expect(at(order, /Scouting at|Team scouting/)).toBeLessThan(at(order, /Eagle Robotics/));
    expect(at(order, /Eagle Robotics/)).toBeLessThan(at(order, /Invite scouts/));
    expect(at(order, /Invite scouts/)).toBeLessThan(at(order, /Members/));
    expect(at(order, /Members/)).toBeLessThan(at(order, /Our season/));
  });

  it('shows a member only what they can use', async () => {
    signInTestWorkspace('member');
    mockRole('member');
    renderAt();
    await screen.findByRole('heading', { name: /Members/ });
    expect(screen.queryByRole('heading', { name: 'Invite scouts' })).toBeNull();
  });

  it('shares the invite link with the share sheet when there is one', async () => {
    signInTestWorkspace('leader');
    mockRole('leader');
    rememberIssuedJoinCode(1, 'ABCDE12345');
    const share = vi.fn(async () => undefined);
    Object.defineProperty(navigator, 'share', { value: share, configurable: true });
    renderAt();
    fireEvent.click(await screen.findByRole('button', { name: 'Share invite link' }));
    await waitFor(() => expect(share).toHaveBeenCalled());
    const url = (share.mock.calls[0] as unknown as [{ url: string }])[0].url;
    expect(url).toMatch(/#\/my-team\?code=ABCDE12345$/);
    expect(copyTextToClipboard).not.toHaveBeenCalled();
  });

  it('copies the invite link when the browser has no share sheet', async () => {
    signInTestWorkspace('leader');
    mockRole('leader');
    rememberIssuedJoinCode(1, 'ABCDE12345');
    renderAt();
    const button = await screen.findByRole('button', { name: 'Share invite link' });
    await act(async () => {
      fireEvent.click(button);
    });
    expect(copyTextToClipboard).toHaveBeenCalledWith(expect.stringMatching(/#\/my-team\?code=ABCDE12345$/));
    expect(await screen.findByRole('button', { name: 'Link copied' })).toBeTruthy();
  });

  it('pre-fills the join code from an invite link without submitting', () => {
    renderAt('/my-team?code=ABCDE12345');
    expect((screen.getByLabelText(/Join code/) as HTMLInputElement).value).toBe('ABCDE12345');
    expect(joinWorkspace).not.toHaveBeenCalled();
  });

  it('still renders when the coverage response is partial', async () => {
    signInTestWorkspace('member');
    mockRole('member');
    vi.mocked(ensureTeamRoom).mockResolvedValue({
      ok: true, created: false, room_key: 'r1', event_key: '2026arc',
      me: { member_id: 1, display_name: 'Test Scout', role: 'member' },
      members: [{ member_id: 1, display_name: 'Test Scout', role: 'member' }],
      assignments: [],
    } as never);
    vi.mocked(getScoutingCoverage).mockResolvedValue({ ok: true } as never);
    renderAt();
    expect(await screen.findByRole('link', { name: /Pit scouting/ })).toBeTruthy();
    await waitFor(() => expect(getScoutingCoverage).toHaveBeenCalled());
    expect(await screen.findByRole('heading', { name: /Members/ })).toBeTruthy();
    expect(screen.queryByText(/failed to load/i)).toBeNull();
  });
});
