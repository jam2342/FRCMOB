// Phone scouting: choosing the robot, remembered views and room tools.
// Shared mocks and setup: scoutingPage.mocks.testkit.ts, scoutingPhone.testkit.tsx.
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { expect, it, vi } from 'vitest';
import { ScoutingPage } from './ScoutingPage';
import { getEventSchedule } from '../api';
import { match, mount, room, setUpPhoneScouting } from './scoutingPhone.testkit';

vi.mock('../api', async () => (await import('./scoutingPage.mocks.testkit')).apiModule());
vi.mock('../hooks/useOnlineStatus', async () => (await import('./scoutingPage.mocks.testkit')).onlineStatusModule);
vi.mock('../features/workspace/useTeamRoom', async () => (await import('./scoutingPage.mocks.testkit')).teamRoomModule);
vi.mock('../components/PageViewBar', () => ({ PageViewBar: () => null }));

setUpPhoneScouting();

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

it('flags a robot to the room the moment a scout taps Broke down, and keeps the tag for the report', async () => {
  room.snapshot = null;
  const sockets: Array<{ send: ReturnType<typeof vi.fn>; onopen?: () => void }> = [];
  vi.stubGlobal('WebSocket',class {
    static OPEN = 1;
    readyState = 1;
    send = vi.fn();
    close = vi.fn();
    constructor() { sockets.push(this as never); }
  });
  mount('/scouting?event=2026week0&match=2026week0_qm1&team=frc254');
  await waitFor(() => expect(screen.getByRole('region',{name:'Mobile scouting quick context'})).toHaveTextContent('#254'));
  fireEvent.click(screen.getByText(/Room tools/));
  fireEvent.change(screen.getByLabelText('Scouting room key'),{target:{value:'room-test'}});
  fireEvent.click(screen.getByRole('button',{name:'Join Room'}));
  await waitFor(() => expect(sockets.length).toBeGreaterThan(0));
  sockets.at(-1)!.onopen?.();
  fireEvent.click(screen.getByRole('tab',{name:'Scout'}));
  fireEvent.click(screen.getByRole('tab',{name:'Teleop'}));
  const flags = screen.getByRole('group',{name:'Flag this robot'});
  fireEvent.click(within(flags).getByRole('button',{name:'Broke down'}));
  expect(within(flags).getByRole('button',{name:'Broke down'})).toHaveAttribute('aria-pressed','true');
  const sent = sockets.at(-1)!.send.mock.calls.map(([body]) => JSON.parse(String(body)));
  expect(sent).toContainEqual({type:'flag',tag:'broke_down',team_key:'frc254',match_key:'2026week0_qm1'});
  // Turning a flag off is not news.
  fireEvent.click(within(flags).getByRole('button',{name:'Broke down'}));
  expect(sockets.at(-1)!.send.mock.calls.filter(([body]) => String(body).includes('"flag"'))).toHaveLength(1);
},20000);
