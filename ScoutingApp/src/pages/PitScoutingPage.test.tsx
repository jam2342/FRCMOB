import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
vi.mock('../hooks/useMobileLayout', () => ({ MOBILE_LAYOUT_BREAKPOINT: 760, useMobileLayout: () => false }));
vi.mock('../components/PageViewBar', () => ({ PageViewBar: () => null }));
vi.mock('../components/EventPicker', () => ({ EventPicker: ({ onSelect, value }: { onSelect: (key: string) => void; value: string }) => <>
  <button onClick={() => onSelect('2026new')}>New event</button>
  <button onClick={() => onSelect(value)}>Load event</button>
</> }));
afterEach(() => { cleanup(); localStorage.clear(); vi.restoreAllMocks(); });
vi.mock('../features/workspace/useWorkspace', () => ({ useWorkspace: () => ({ workspace: { id: 1 }, me: { role: 'leader' } }) }));
vi.mock('../features/workspace/WorkspaceGate', () => ({ WorkspaceGate: ({ children }: { children: React.ReactNode }) => children }));
import { PitScoutingPage } from './PitScoutingPage';
import { QueuedForSyncError, upsertPitEntry } from '../api';
import { readPitDraft } from '../features/offline/pitDrafts';
const queue = vi.hoisted(() => ({ rows: [] as Array<{id: string; url: string; body: string; queuedAt: string; failure?: {reason: string}}> }));
vi.mock('../utils/offlineQueue', () => ({ listQueuedMutations: vi.fn(async () => queue.rows) }));
vi.mock('../api', async original => ({ ...(await original<typeof import('../api')>()), getEventTeamsIntel: vi.fn(async () => ({ teams: [{team_key:'frc254', team_number:254}] })), listPitEntries: vi.fn(async () => ({entries:[]})), upsertPitEntry: vi.fn() }));
it('keeps a rejected specific save and its draft after pending count reaches zero', async () => {
  Element.prototype.scrollIntoView = vi.fn();
  vi.mocked(upsertPitEntry).mockImplementation(async payload => {
    queue.rows = [{id:'pit-save',url:'/pit-scouting',body:JSON.stringify(payload),queuedAt:new Date().toISOString()}];
    throw new QueuedForSyncError('queued');
  });
  render(<MemoryRouter initialEntries={['/?event=2026old']}><PitScoutingPage /></MemoryRouter>);
  fireEvent.click(await screen.findByRole('button', {name: /254/}));
  const input = screen.getAllByRole('spinbutton')[0];
  fireEvent.change(input, {target:{value:'5'}});
  fireEvent.click(screen.getAllByRole('button', {name:'Save entry'})[0]);
  await screen.findByText(/Saved #254 on this phone/);
  expect(readPitDraft(1,'2026old','frc254')).not.toBeNull();
  await act(async () => { queue.rows[0].failure = {reason:'conflict'}; window.dispatchEvent(new CustomEvent('offlinequeue:dropped')); window.dispatchEvent(new CustomEvent('offlinequeue:change',{detail:{count:0}})); });
  await screen.findByText("Your team's server didn't accept this save. Your notes are still here; try saving again.");
  expect(screen.queryByText('Synced with your team.')).not.toBeInTheDocument();
  expect(readPitDraft(1,'2026old','frc254')).not.toBeNull();
});
it('confirms only the specific save even with unrelated items pending', async () => {
  Element.prototype.scrollIntoView = vi.fn();
  vi.mocked(upsertPitEntry).mockImplementation(async payload => {
    queue.rows = [{id:'pit-save',url:'/pit-scouting',body:JSON.stringify(payload),queuedAt:new Date().toISOString()}];
    throw new QueuedForSyncError('queued');
  });
  render(<MemoryRouter initialEntries={['/?event=2026old']}><PitScoutingPage /></MemoryRouter>);
  fireEvent.click(await screen.findByRole('button',{name:/254/}));
  fireEvent.change(screen.getAllByRole('spinbutton')[0],{target:{value:'5'}});
  fireEvent.click(screen.getAllByRole('button',{name:'Save entry'})[0]);
  await screen.findByText(/Saved #254 on this phone/);
  await act(async () => { queue.rows = [{id:'other',url:'/other',body:'{}',queuedAt:new Date().toISOString()}]; window.dispatchEvent(new CustomEvent('offlinequeue:change',{detail:{count:1}})); });
  await screen.findByText('Synced with your team.');
  expect(readPitDraft(1,'2026old','frc254')).toBeNull();
});
