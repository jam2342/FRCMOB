// Phone scouting: the Robot / Scout / Saved tabs and the phase tabs.
// Shared mocks and setup: scoutingPage.mocks.testkit.ts, scoutingPhone.testkit.tsx.
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { EMPTY_FORM, readScoutDraft, scoutDraftKey, writeScoutDraft } from './scoutingPage.helpers';
import { mount, room, setUpPhoneScouting } from './scoutingPhone.testkit';

vi.mock('../api', async () => (await import('./scoutingPage.mocks.testkit')).apiModule());
vi.mock('../hooks/useOnlineStatus', async () => (await import('./scoutingPage.mocks.testkit')).onlineStatusModule);
vi.mock('../features/workspace/useTeamRoom', async () => (await import('./scoutingPage.mocks.testkit')).teamRoomModule);
vi.mock('../components/PageViewBar', () => ({ PageViewBar: () => null }));

setUpPhoneScouting();

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
