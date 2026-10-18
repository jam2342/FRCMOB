// findBy* waits get 5 s: CI runners missed the 1 s default.
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ScoutingPage } from './ScoutingPage';
import { getEventSchedule } from '../api';
import { EMPTY_FORM, readScoutDraft, scoutDraftKey, writeScoutDraft } from './scoutingPage.helpers';
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


const room = vi.hoisted(() => ({ snapshot: null as unknown, schedule: [] as unknown[], retry: vi.fn(async () => {}) }));
vi.mock('../features/workspace/useTeamRoom', () => ({
  isTeamRoomKey: () => true,
  useTeamRoom: () => ({ snapshot: room.snapshot, schedule: room.schedule, status: 'ready', isLeader: false, retry: room.retry }),
}));
vi.mock('../components/PageViewBar', () => ({ PageViewBar: () => null }));

const match = (number: number) => ({
  match_key: `2026week0_qm${number}`, display_name: `Qual ${number}`, comp_level: 'qm', set_number: 1, match_number: number,
  is_completed: false, scheduled_time: null,
  red: [{team_key:'frc118',team_number:118,station:'r1'}, {team_key:'frc254',team_number:254,station:'r2'}], blue: [],
});
const mount = (path = '/scouting?event=2026week0') => render(<MemoryRouter initialEntries={[path]}><ScoutingPage /></MemoryRouter>);
beforeEach(() => {
  localStorage.clear(); sessionStorage.clear();
  room.retry.mockClear();
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
  writeScoutDraft(key,{form:{...EMPTY_FORM,auto_scored:7},notes:'Keep this draft',saved_at_ms:1});
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
  expect(screen.getByRole('region',{name:'Mobile scouting quick context'})).toHaveTextContent('#254');
  // The editor shows the resumed counter, not just the stored copy.
  await waitFor(() => expect(screen.getAllByLabelText(/Auto fuel scored/i).find((el) => el.tagName === 'INPUT')).toHaveValue(7));
  await waitFor(() => expect(readScoutDraft<typeof EMPTY_FORM>(key)?.form.auto_scored).toBe(7));
  fireEvent.click(screen.getByRole('tab',{name:'Robot'}));
  fireEvent.click(screen.getByRole('button',{name:'Choose another robot'}));
  expect(screen.getByRole('button', {name:'Qual 1'})).toBeInTheDocument();
},20000);
it('puts every phone section on visible Robot, Scout and Saved tabs without dropdowns', async () => {
  room.snapshot = null;
  const { container } = mount();
  const robot = await screen.findByRole('button',{name:/#254.*Red 2/});
  expect(robot).not.toHaveClass('active');
  expect(screen.getByRole('tab',{name:'Robot'})).toHaveAttribute('aria-selected','true');
  for (const name of ['Scout','Saved']) expect(screen.getByRole('tab',{name})).toHaveAttribute('aria-selected','false');
  expect(container.querySelector('select')).toBeNull();
  fireEvent.click(robot);
  expect(screen.getByRole('tab',{name:'Scout'})).toHaveAttribute('aria-selected','true');
  for (const phase of ['Auto','Teleop','Endgame','Review']) {
    fireEvent.click(screen.getByRole('tab',{name:phase}));
    expect(screen.getByRole('tab',{name:phase})).toHaveAttribute('aria-selected','true');
    expect(container.querySelector('select')).toBeNull();
    expect(screen.queryByLabelText('More scouting panels')).not.toBeInTheDocument();
  }
  fireEvent.click(screen.getByRole('tab',{name:'Saved'}));
  expect(screen.getByRole('tab',{name:'Saved'})).toHaveAttribute('aria-selected','true');
  expect(screen.getByRole('button',{name:'Export CSV'})).toBeInTheDocument();
  expect(screen.getByRole('heading', {name:'Scouted Matches'}).compareDocumentPosition(screen.getByRole('heading', {name:'Backup & sharing'})) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(screen.getByRole('button',{name:'Import QR'})).toBeInTheDocument();
  expect(screen.queryByRole('button',{name:'Export HTML'})).not.toBeInTheDocument();
  expect(screen.queryByLabelText('More scouting views')).not.toBeInTheDocument();
  expect(container.querySelector('select')).toBeNull();
},20000);

it('groups Auto, Teleop, Endgame and Review fields in their phone phases', async () => {
  room.snapshot = null;
  mount();
  fireEvent.click(await screen.findByRole('button',{name:/#254.*Red 2/}));
  fireEvent.click(screen.getByRole('tab',{name:'Auto'}));
  for (const name of ['Auto fuel scored','Auto misses','Auto pickups','Auto cycles']) expect(screen.getByLabelText(name)).toBeInTheDocument();
  expect(screen.getByRole('switch',{name:/Mobility/})).toBeInTheDocument();
  expect(screen.getByRole('group',{name:'Auto path quality'})).toBeInTheDocument();
  expect(screen.queryByRole('link',{name:/Draw auto path/})).not.toBeInTheDocument();
  expect(screen.queryByLabelText('Bump crossings')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('tab',{name:'Teleop'}));
  for (const name of ['Teleop fuel scored','Teleop misses','Scored while defended','Attempts while defended','Teleop cycles','Drops/lost pieces','Intake failures','Fouls / penalties','Bump crossings','Trench crossings']) expect(screen.getByLabelText(name)).toBeInTheDocument();
  expect(screen.getByLabelText('Teleop fuel scored').compareDocumentPosition(screen.getByLabelText('Bump crossings')) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(screen.queryByRole('group',{name:'Anti-defense level'})).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('tab',{name:'Endgame'}));
  expect(screen.getByRole('group',{name:'Endgame result'})).toBeInTheDocument();
  expect(screen.getByRole('switch',{name:/Climbed while defended/})).toBeInTheDocument();
  expect(screen.getByRole('switch',{name:/Protected-zone risk/})).toBeInTheDocument();
  expect(screen.queryByText('RP Checks')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('tab',{name:'Review'}));
  const points = screen.getByText('Point Summary');
  const live = screen.getByText('Live Robot Rating');
  const notes = screen.getByLabelText('Scouting notes');
  expect(points.compareDocumentPosition(live) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(live.compareDocumentPosition(notes) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  for (const name of ['Offense level','Defense level','Field awareness','Decision quality','Communication','Anti-defense level','Escape from pin','Path reroute','Recovery after contact']) {
    const rating = screen.getByRole('group',{name});
    expect(notes.compareDocumentPosition(rating) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  }
},20000);

it('retains an unsaved report while switching all three phone tabs and phases', async () => {
  room.snapshot = null;
  mount('/scouting?event=2026week0&match=2026week0_qm1&team=frc254');
  await waitFor(() => expect(screen.getByRole('region',{name:'Mobile scouting quick context'})).toHaveTextContent('#254'));
  fireEvent.click(screen.getByRole('tab',{name:'Auto'}));
  fireEvent.change(screen.getByLabelText('Auto fuel scored'),{target:{value:'12'}});
  fireEvent.click(screen.getByRole('tab',{name:'Teleop'}));
  fireEvent.change(screen.getByLabelText('Trench crossings'),{target:{value:'4'}});
  fireEvent.click(screen.getByRole('tab',{name:'Review'}));
  fireEvent.change(screen.getByLabelText('Scouting notes'),{target:{value:'Keep my report'}});
  for (const tab of ['Saved','Robot','Scout']) fireEvent.click(screen.getByRole('tab',{name:tab}));
  expect(screen.getByRole('tab',{name:'Review'})).toHaveAttribute('aria-selected','true');
  expect(screen.getByLabelText('Scouting notes')).toHaveValue('Keep my report');
  fireEvent.click(screen.getByRole('tab',{name:'Auto'}));
  expect(screen.getByLabelText('Auto fuel scored')).toHaveValue(12);
  fireEvent.click(screen.getByRole('tab',{name:'Teleop'}));
  expect(screen.getByLabelText('Trench crossings')).toHaveValue(4);
  await waitFor(() => expect(readScoutDraft<typeof EMPTY_FORM>(scoutDraftKey(1,'2026week0_qm1','frc254'))?.notes).toBe('Keep my report'));
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
  await screen.findByRole('button',{name:/#254.*Red 2/});
  fireEvent.click(screen.getByRole('tab',{name:'Rapid Tap'}));
  const { act } = await import('@testing-library/react');
  act(() => listeners.forEach(listener => listener({matches:true})));
  fireEvent.click(screen.getByRole('button',{name:/#254.*Red 2/}));
  fireEvent.change(screen.getByLabelText('Auto fuel scored'),{target:{value:'9'}});
  fireEvent.click(screen.getByRole('button',{name:'Save'}));
  await screen.findByText(/Saved #254/);
  expect(screen.getByLabelText('Auto fuel scored')).toHaveValue(9);
  const { readStoredEntries } = await import('./scoutingPage.helpers');
  expect(readStoredEntries(1)[0].mode).toBe('match');
  fireEvent.click(screen.getByRole('tab',{name:'Saved'}));
  expect(screen.getByText('Scouted Matches')).toBeInTheDocument();
  expect(screen.getAllByText('#254').length).toBeGreaterThan(0);
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
  fireEvent.click(await screen.findByRole('button',{name:/#118.*Red 1/}));
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
  await screen.findByRole('button',{name:/#254.*Red 2/});
  expect(screen.queryByText(/Upcoming Opponents/)).not.toBeInTheDocument();
});

it('clears a robot that is not in the newly chosen match', async () => {
  room.snapshot = null;
  room.schedule = [match(1), { ...match(3), red: [{team_key:'frc1',team_number:1,station:'r1'}], blue: [{team_key:'frc2',team_number:2,station:'b1'}] }];
  vi.mocked(getEventSchedule).mockResolvedValue({matches:room.schedule,event_name:'Week 0'} as never);
  mount('/scouting?event=2026week0&match=2026week0_qm1&team=frc254');
  await waitFor(() => expect(screen.getByRole('region',{name:'Mobile scouting quick context'})).toHaveTextContent('#254'));
  fireEvent.click(screen.getByRole('tab',{name:'Robot'}));
  fireEvent.click(screen.getByRole('button',{name:'Qual 3'}));
  await waitFor(() => expect(screen.queryByRole('button',{name:/#254.*Red 2/})).not.toBeInTheDocument());
  fireEvent.click(screen.getByRole('tab',{name:'Scout'}));
  expect(screen.getByRole('region',{name:'Mobile scouting quick context'})).not.toHaveTextContent('#254');
  expect(screen.getByText('Pick the team you are scouting.')).toBeInTheDocument();
},20000);

it.each([
  ['strategy','capture','Review'],
  ['mobility','capture','Review'],
  ['notes','capture','Review'],
  ['teleop','score','Review'],
  ['auto-paths','capture','Auto'],
  ['teleop','history','Saved'],
])('migrates remembered %s / %s to the visible %s view', async (capture, section, expected) => {
  room.snapshot = null;
  localStorage.setItem('scouting_mobile_panel_prefs_v1',JSON.stringify({finderOpen:false,section,capture,score:'live'}));
  mount();
  await waitFor(() => expect(screen.getByRole('tab',{name:expected})).toHaveAttribute('aria-selected','true'));
  expect(screen.queryByLabelText('More scouting panels')).not.toBeInTheDocument();
},20000);

it('keeps reconnect beside the phone counters and room tools at the bottom of Robot', async () => {
  room.snapshot = null;
  const { container } = mount();
  const robot = await screen.findByRole('button',{name:/#254.*Red 2/});
  const summary = screen.getByText(/Room tools/);
  fireEvent.click(screen.getByRole('button',{name:'Reconnect'}));
  expect(room.retry).toHaveBeenCalledTimes(1);
  expect(summary.tagName).toBe('SUMMARY');
  expect(summary.closest('details')).not.toHaveAttribute('open');
  expect(screen.getByRole('button',{name:'Reconnect'})).toBeInTheDocument();
  fireEvent.click(summary);
  expect(summary.closest('details')).toHaveAttribute('open');
  expect(screen.getByLabelText('Scouting room key')).toBeInTheDocument();
  expect(container.querySelector('select')).toBeNull();
  fireEvent.click(robot);
  expect(screen.getByRole('button',{name:'Reconnect'})).toBeInTheDocument();
  expect(screen.getByRole('tab',{name:'Scout'})).toHaveAttribute('aria-selected','true');
},20000);

it('keeps the existing desktop groups and dropdown fields', async () => {
  room.snapshot = null;
  vi.stubGlobal('matchMedia', () => ({matches:false,addEventListener:vi.fn(),removeEventListener:vi.fn()}));
  mount();
  await screen.findByRole('button',{name:/#254.*Red 2/});
  expect(screen.getByRole('tab',{name:'Rapid Tap'})).toBeInTheDocument();
  expect(screen.getByLabelText('Match').tagName).toBe('SELECT');
  expect(screen.getByLabelText('Endgame result').tagName).toBe('SELECT');
  for (const name of ['Auto fuel scored','Teleop fuel scored','Bump crossings','Trench crossings']) expect(screen.getByLabelText(name)).toBeInTheDocument();
  expect(screen.getByRole('group',{name:'Anti-defense level'})).toBeInTheDocument();
  expect(screen.queryByRole('tab',{name:'Review'})).not.toBeInTheDocument();
  // Desktop's side panel uses the phone's names for the same three sections.
  for (const name of ['Robot','Room','Saved']) expect(screen.getByRole('tab',{name})).toBeInTheDocument();
},20000);

it('cancels a match change without losing a draft when phone storage is full', async () => {
  room.snapshot = null;
  mount('/scouting?event=2026week0&match=2026week0_qm1&team=frc254');
  await waitFor(() => expect(screen.getByRole('region',{name:'Mobile scouting quick context'})).toHaveTextContent('#254'));
  fireEvent.click(screen.getByRole('tab',{name:'Auto'}));
  const original = Storage.prototype.setItem;
  vi.spyOn(Storage.prototype,'setItem').mockImplementation(function(this:Storage,key:string,value:string) {
    if (key.startsWith('frcmob_scout_draft_v1:')) throw new Error('Storage full');
    return original.call(this,key,value);
  });
  fireEvent.change(screen.getByLabelText('Auto fuel scored'),{target:{value:'15'}});
  await screen.findByText(/Device storage is full or unavailable/);
  fireEvent.click(screen.getByRole('tab',{name:'Robot'}));
  const choices = within(screen.getByRole('group',{name:'Match'})).getAllByRole('button');
  const current = choices.find(choice => choice.getAttribute('aria-pressed') === 'true')!;
  const other = choices.find(choice => choice.getAttribute('aria-pressed') === 'false')!;
  const confirm = vi.spyOn(window,'confirm').mockReturnValue(false);
  fireEvent.click(other);
  expect(confirm).toHaveBeenCalledWith('This draft could not be saved on your device. Discard it and choose another robot?');
  expect(current).toHaveAttribute('aria-pressed','true');
  fireEvent.click(screen.getByRole('tab',{name:'Scout'}));
  expect(screen.getByLabelText('Auto fuel scored')).toHaveValue(15);
},20000);

it('keeps a joined room key and Copy visible when Room tools is closed', async () => {
  room.snapshot = null;
  vi.stubGlobal('WebSocket',class {
    static OPEN = 1;
    readyState = 0;
    send = vi.fn();
    close = vi.fn();
  });
  mount();
  await screen.findByRole('button',{name:/#254.*Red 2/});
  const summary = screen.getByText(/Room tools/);
  fireEvent.click(summary);
  fireEvent.change(screen.getByLabelText('Scouting room key'),{target:{value:'room-test'}});
  fireEvent.click(screen.getByRole('button',{name:'Join Room'}));
  const status = screen.getByRole('status',{name:'Room connection'});
  await waitFor(() => expect(within(status).getByRole('button',{name:'Copy Key'})).toBeInTheDocument());
  fireEvent.click(summary);
  expect(summary.closest('details')).not.toHaveAttribute('open');
  expect(status).toHaveTextContent('room-test');
  expect(within(status).getByRole('button',{name:'Copy Key'})).toBeInTheDocument();
},20000);

it('offers Undo after a save that turns the report back into a draft', async () => {
  room.snapshot = null;
  const { currentToast, runToastAction } = await import('../components/ui/toast');
  const { readStoredEntries } = await import('./scoutingPage.helpers');
  mount('/scouting?event=2026week0&match=2026week0_qm1&team=frc254');
  await waitFor(() => expect(screen.getByRole('region',{name:'Mobile scouting quick context'})).toHaveTextContent('#254'));
  fireEvent.click(screen.getByRole('tab',{name:'Auto'}));
  fireEvent.click(screen.getByRole('button',{name:/^Increase Auto fuel scored/}));
  const counted = Number((screen.getByLabelText('Auto fuel scored') as HTMLInputElement).value);
  expect(counted).toBeGreaterThan(0);
  fireEvent.click(screen.getByRole('button',{name:'Save'}));
  await screen.findByText(/Saved #254/);
  expect(readStoredEntries(1)).toHaveLength(1);
  const toast = currentToast();
  expect(toast?.message).toBe('Saved #254 in Qual 1');
  expect(toast?.actionLabel).toBe('Undo');
  const { act } = await import('@testing-library/react');
  act(() => runToastAction(toast!.id));
  expect(readStoredEntries(1)).toHaveLength(0);
  expect(readScoutDraft<typeof EMPTY_FORM>(scoutDraftKey(1,'2026week0_qm1','frc254'))?.form.auto_scored).toBe(counted);
  expect(screen.getByLabelText('Auto fuel scored')).toHaveValue(counted);
  await screen.findByText(/Undone\. This report is a draft again/);
},20000);

it('closes the Undo toast when the scout leaves the board, keeping the saved report', async () => {
  room.snapshot = null;
  const { currentToast } = await import('../components/ui/toast');
  const { readStoredEntries } = await import('./scoutingPage.helpers');
  const view = mount('/scouting?event=2026week0&match=2026week0_qm1&team=frc254');
  await waitFor(() => expect(screen.getByRole('region',{name:'Mobile scouting quick context'})).toHaveTextContent('#254'));
  fireEvent.click(screen.getByRole('button',{name:'Save'}));
  await screen.findByText(/Saved #254/);
  expect(currentToast()).not.toBeNull();
  view.unmount();
  expect(currentToast()).toBeNull();
  expect(readStoredEntries(1)).toHaveLength(1);
},20000);
