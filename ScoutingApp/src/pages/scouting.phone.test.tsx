// findBy* waits get 5 s: CI runners missed the 1 s default.
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ScoutingPage } from './ScoutingPage';
import { getEventSchedule } from '../api';
import { EMPTY_FORM, EMPTY_RP, readScoutDraft, scoutDraftKey, writeScoutDraft } from './scoutingPage.helpers';
import { signInTestWorkspace } from '../test/workspace';

vi.mock('../api', () => ({
  clientAdminKeyAvailable: () => false,
  addScoutingRoomSecondaryLeader: vi.fn(async () => ({
    ok: true,
    room_key: 'room-test',
    target_scout_profile: 'Scout B',
    secondary_leader_scout_profiles: ['Scout B'],
    created: true,
  })),
  clearStoredRoomAccessToken: vi.fn(),
  createOrJoinScoutingRoom: vi.fn(async () => ({
    room: {
      room_key: 'room-test',
      event_key: '2026week0',
      title: 'Test Room',
      created_at: Date.now(),
      updated_at: Date.now(),
      created_by: 'Scout A',
      secondary_leader_scout_profiles: [],
      presence: [{ scout_profile: 'Scout A', connections: 1 }, { scout_profile: 'Scout B', connections: 1 }],
      room_role: 'owner',
    },
    entries: [],
  })),
  getEventSchedule: vi.fn(async () => ({
    ok: true,
    event_key: '2026week0',
    event_name: 'Week 0',
    count: 0,
    matches: [],
  })),
  getEventTeamsIntel: vi.fn(async () => ({
    ok: true,
    event_key: '2026week0',
    count: 0,
    teams: [],
  })),
  getSuggestedEvents: vi.fn(async () => ({
    ok: true,
    preferred_year: 2026,
    fallback_year: 2025,
    selected_year: 2026,
    source: 'test',
    count: 0,
    events: [],
  })),
  getStoredRoomAccessToken: vi.fn(() => 'token-test'),
  getTeamIntel: vi.fn(async () => ({})),
  removeScoutingRoomSecondaryLeader: vi.fn(async () => ({
    ok: true,
    room_key: 'room-test',
    target_scout_profile: 'Scout B',
    secondary_leader_scout_profiles: [],
    removed: true,
  })),
  saveScoutingRoomEntry: vi.fn(async ({ entry }: { entry: unknown }) => ({
    ok: true,
    entry: {
      room_key: 'room-test',
      entry,
    },
  })),
  setStoredRoomAccessToken: vi.fn(),
  scoutingRoomWebSocketUrl: vi.fn(() => 'ws://localhost/test-room'),
  // No team room in these tests: they drive the keyed room path.
  ensureTeamRoom: vi.fn(async () => { throw new Error('no team room in this test'); }),
  getTeamRoom: vi.fn(async () => null),
}));

// The online monitor pings the real API; when that answer lands mid-test it
// re-renders the page at a random moment. These tests are about rooms.
vi.mock('../hooks/useOnlineStatus', () => ({
  useOnlineStatus: () => ({ online: true, queueSize: 0, isShowingOfflineData: false }),
}));


const room = vi.hoisted(() => ({ snapshot: null as unknown, schedule: [] as unknown[] }));
vi.mock('../features/workspace/useTeamRoom', () => ({
  isTeamRoomKey: () => true,
  useTeamRoom: () => ({ snapshot: room.snapshot, schedule: room.schedule, status: 'ready', isLeader: false }),
}));
vi.mock('../components/PageViewBar', () => ({ PageViewBar: () => null }));

const match = (number: number) => ({
  match_key: `2026week0_qm${number}`, display_name: `Qual ${number}`, comp_level: 'qm', set_number: 1, match_number: number,
  is_completed: false, scheduled_time: null,
  red: [{team_key:'frc118',team_number:118,station:'r1'}, {team_key:'frc254',team_number:254,station:'r2'}], blue: [],
});
const mount = () => render(<MemoryRouter initialEntries={['/scouting?event=2026week0']}><ScoutingPage /></MemoryRouter>);
beforeEach(() => {
  localStorage.clear(); sessionStorage.clear();
  signInTestWorkspace('member');
  vi.stubGlobal('matchMedia', () => ({matches:true,addEventListener:vi.fn(),removeEventListener:vi.fn()}));
  Element.prototype.scrollIntoView = vi.fn();
  room.schedule = [match(1),match(2)];
  room.snapshot = {room_key:'room-test', me:{member_id:1}, assignments:[{match_key:'2026week0_qm2',team_key:'frc254',assigned_member_id:1,covered_by_me:false}]};
  vi.mocked(getEventSchedule).mockResolvedValue({matches:room.schedule,event_name:'Week 0'} as never);
});
afterEach(() => {cleanup();vi.unstubAllGlobals();vi.restoreAllMocks();});
it('starts the next assignment on Auto with its robot, station and existing draft', async () => {
  const key = scoutDraftKey(1,'2026week0_qm2','frc254');
  writeScoutDraft(key,{form:{...EMPTY_FORM,auto_scored:7},rp:EMPTY_RP,notes:'Keep this draft',saved_at_ms:1});
  mount();
  const heading = await screen.findByText('Your next assignment');
  expect(heading.closest('section')).toHaveTextContent('Qual 2 · Team 254');
  expect(heading.closest('section')).toHaveTextContent('Red 2');
  expect(screen.queryByLabelText('Match')).not.toBeInTheDocument();
  expect(screen.queryByLabelText('Scout name')).not.toBeInTheDocument();
  fireEvent.click(within(heading.closest('section')!).getByRole('button',{name:'Start'}));
  expect(screen.getByRole('tab',{name:'Scout'})).toHaveAttribute('aria-selected','true');
  expect(screen.getByRole('tab',{name:'Auto'})).toHaveAttribute('aria-selected','true');
  expect(screen.getByRole('region',{name:'Mobile scouting quick context'})).toHaveTextContent('Qual 2');
  expect(screen.getByRole('region',{name:'Mobile scouting quick context'})).toHaveTextContent('FRC254');
  // The editor shows the resumed counter, not just the stored copy.
  await waitFor(() => expect(screen.getAllByLabelText(/Auto fuel scored/i).find((el) => el.tagName === 'INPUT')).toHaveValue(7));
  await waitFor(() => expect(readScoutDraft<typeof EMPTY_FORM,typeof EMPTY_RP>(key)?.form.auto_scored).toBe(7));
  fireEvent.click(screen.getByRole('tab',{name:'Robot'}));
  fireEvent.click(screen.getByRole('button',{name:'Choose another robot'}));
  expect(screen.getByLabelText('Match')).toBeInTheDocument();
},20000);
it('uses one phone mode, opens a chosen robot and keeps phases and secondary panels available', async () => {
  room.snapshot = null;
  mount();
  const robot = await screen.findByRole('button',{name:/FRC254.*Red 2/});
  expect(robot).not.toHaveClass('active');
  expect(screen.queryByRole('tab',{name:'Setup'})).not.toBeInTheDocument();
  expect(screen.queryByRole('tab',{name:'Room'})).not.toBeInTheDocument();
  expect(screen.queryByRole('tab',{name:'Data'})).not.toBeInTheDocument();
  expect(screen.queryByRole('tab',{name:'Rapid Tap'})).not.toBeInTheDocument();
  expect(screen.queryByRole('tab',{name:'Match Scout'})).not.toBeInTheDocument();
  fireEvent.click(robot);
  expect(screen.getByRole('tab',{name:'Scout'})).toHaveAttribute('aria-selected','true');
  expect(screen.queryByRole('tab',{name:'Capture'})).not.toBeInTheDocument();
  expect(screen.queryByRole('tab',{name:'Summary'})).not.toBeInTheDocument();
  for (const phase of ['Auto','Teleop','Endgame']) {
    fireEvent.click(screen.getByRole('tab',{name:phase}));
    expect(screen.getByRole('tab',{name:phase})).toHaveAttribute('aria-selected','true');
  }
  const more = screen.getByLabelText('More scouting panels');
  for (const value of ['mobility','strategy','notes','summary-points','summary-live']) {
    fireEvent.change(more,{target:{value}});
    // The panel opens; the ⋯ box itself stays on ⋯ (fixed width at 250px).
    expect(more).toHaveValue('');
    expect(screen.getByRole('tab',{name:'Auto'})).toHaveAttribute('aria-selected','false');
  }
  const views = screen.getByLabelText('More scouting views');
  fireEvent.change(views,{target:{value:'room'}});
  expect(screen.getByText('Scout Profile')).toBeInTheDocument();
  fireEvent.change(views,{target:{value:'data'}});
  expect(screen.getByText('Save + Data')).toBeInTheDocument();
  expect(screen.queryByRole('button',{name:'Save Scouting Entry'})).not.toBeInTheDocument();
  expect(screen.queryByRole('button',{name:'Export HTML'})).not.toBeInTheDocument();
  expect(screen.getByRole('button',{name:'Export CSV'})).toBeInTheDocument();
  expect(screen.getByRole('button',{name:'Import QR'})).toBeInTheDocument();
},20000);
it('asks a solo scout for their missing name in Setup without interrupting typing', async () => {
  localStorage.clear();
  const { clearWorkspaceSession } = await import('../features/workspace/workspaceSession');
  clearWorkspaceSession('left'); room.snapshot = null;
  mount();
  const name = screen.getByLabelText('Scout name');
  fireEvent.change(name,{target:{value:'J'}});
  expect(name).toBeInTheDocument();
  fireEvent.change(name,{target:{value:'Jamal'}});
  expect(name).toHaveValue('Jamal');
});
it('keeps Match Scout values after saving on a phone even after desktop Rapid Tap was selected', async () => {
  room.snapshot = null;
  const listeners: Array<(event: {matches:boolean}) => void> = [];
  vi.stubGlobal('matchMedia', () => ({matches:false,addEventListener:(_name:string,listener:(event:{matches:boolean})=>void)=>listeners.push(listener),removeEventListener:vi.fn()}));
  mount();
  await screen.findByRole('button',{name:/FRC254.*Red 2/});
  fireEvent.click(screen.getByRole('tab',{name:'Rapid Tap'}));
  const { act } = await import('@testing-library/react');
  act(() => listeners.forEach(listener => listener({matches:true})));
  fireEvent.click(screen.getByRole('button',{name:/FRC254.*Red 2/}));
  fireEvent.change(screen.getByLabelText('Auto fuel scored'),{target:{value:'9'}});
  fireEvent.click(screen.getByRole('button',{name:'Save'}));
  await screen.findByText(/Saved FRC254/);
  expect(screen.getByLabelText('Auto fuel scored')).toHaveValue(9);
  const { readStoredEntries } = await import('./scoutingPage.helpers');
  expect(readStoredEntries(1)[0].mode).toBe('match');
  fireEvent.change(screen.getByLabelText('More scouting views'),{target:{value:'data'}});
  const confirm = vi.spyOn(window,'confirm').mockReturnValue(false);
  fireEvent.click(screen.getByRole('button',{name:'Clear All'}));
  expect(confirm).toHaveBeenCalledWith('Clear all local scouting entries on this device?');
  expect(readStoredEntries(1)).toHaveLength(1);
  confirm.mockReturnValue(true);
  fireEvent.click(screen.getByRole('button',{name:'Clear All'}));
  expect(readStoredEntries(1)).toHaveLength(0);
},20000);
it('confirms before leaving a draft when device storage has failed', async () => {
  mount();
  await screen.findByText('Your next assignment');
  fireEvent.click(screen.getByRole('button',{name:'Choose another robot'}));
  fireEvent.click(await screen.findByRole('button',{name:/FRC118.*Red 1/}));
  const original = Storage.prototype.setItem;
  vi.spyOn(Storage.prototype,'setItem').mockImplementation(function(this:Storage,key:string,value:string) {
    if (key.startsWith('frcmob_scout_draft_v1:')) throw new Error('Storage full');
    return original.call(this,key,value);
  });
  fireEvent.change(screen.getByLabelText('Auto fuel scored'),{target:{value:'6'}});
  await screen.findByText(/Device storage is full or unavailable/);
  fireEvent.click(screen.getByRole('tab',{name:'Robot'}));
  const confirm = vi.spyOn(window,'confirm').mockReturnValue(false);
  fireEvent.click(screen.getByRole('button',{name:'Start'}));
  expect(confirm).toHaveBeenCalledWith('This draft could not be saved on your device. Discard it and choose another robot?');
  expect(screen.getByRole('tab',{name:'Robot'})).toHaveAttribute('aria-selected','true');
  fireEvent.click(screen.getByRole('tab',{name:'Scout'}));
  expect(screen.getByLabelText('Auto fuel scored')).toHaveValue(6);
},20000);

it('keeps Upcoming Opponents out of phone Setup when my team is configured', async () => {
  room.snapshot = null;
  render(<MemoryRouter initialEntries={['/scouting?event=2026week0&my_team=frc118']}><ScoutingPage /></MemoryRouter>);
  await screen.findByRole('button',{name:/FRC254.*Red 2/});
  expect(screen.queryByText(/Upcoming Opponents/)).not.toBeInTheDocument();
});

it('clears a robot that is not in the newly chosen match', async () => {
  room.snapshot = null;
  room.schedule = [match(1), { ...match(3), red: [{team_key:'frc1',team_number:1,station:'r1'}], blue: [{team_key:'frc2',team_number:2,station:'b1'}] }];
  vi.mocked(getEventSchedule).mockResolvedValue({matches:room.schedule,event_name:'Week 0'} as never);
  mount();
  fireEvent.click(await screen.findByRole('button',{name:/FRC254.*Red 2/}));
  expect(screen.getByRole('region',{name:'Mobile scouting quick context'})).toHaveTextContent('FRC254');
  fireEvent.click(screen.getByRole('tab',{name:'Robot'}));
  fireEvent.change(screen.getByLabelText('Match'),{target:{value:'2026week0_qm3'}});
  await waitFor(() => expect(screen.queryByRole('button',{name:/FRC254/})).not.toBeInTheDocument());
  fireEvent.click(screen.getByRole('tab',{name:'Scout'}));
  expect(screen.getByRole('region',{name:'Mobile scouting quick context'})).not.toHaveTextContent('FRC254');
  expect(screen.getByText('Pick the team you are scouting.')).toBeInTheDocument();
},20000);
