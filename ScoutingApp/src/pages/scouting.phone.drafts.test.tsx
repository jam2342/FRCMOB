// Phone scouting: drafts, saving, Undo and full device storage.
// Shared mocks and setup: scoutingPage.mocks.testkit.ts, scoutingPhone.testkit.tsx.
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { EMPTY_FORM, readScoutDraft, scoutDraftKey } from './scoutingPage.helpers';
import { mount, room, setUpPhoneScouting } from './scoutingPhone.testkit';

vi.mock('../api', async () => (await import('./scoutingPage.mocks.testkit')).apiModule());
vi.mock('../hooks/useOnlineStatus', async () => (await import('./scoutingPage.mocks.testkit')).onlineStatusModule);
vi.mock('../features/workspace/useTeamRoom', async () => (await import('./scoutingPage.mocks.testkit')).teamRoomModule);
vi.mock('../components/PageViewBar', () => ({ PageViewBar: () => null }));

setUpPhoneScouting();

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
