import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
vi.mock('../components/PageViewBar', () => ({ PageViewBar: () => null }));
vi.mock('../components/EventPicker', () => ({ EventPicker: ({ onSelect, value }: { onSelect: (key: string) => void; value: string }) => <>
  <button onClick={() => onSelect('2026new')}>New event</button>
  <button onClick={() => onSelect(value)}>Load event</button>
</> }));
afterEach(() => { cleanup(); localStorage.clear(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
vi.mock('../features/workspace/useWorkspace', () => ({ useWorkspace: () => ({ workspace: { id: 1 }, me: { role: 'leader' } }) }));
vi.mock('../features/workspace/WorkspaceGate', () => ({ WorkspaceGate: ({ children }: { children: React.ReactNode }) => children }));
import { useCallback, useState } from 'react';
import { PicklistPage } from './PicklistPage';
import type { Picklist } from '../api';
const saved = vi.hoisted(() => ({ edit: vi.fn() }));
const picklist = { id:1, title:'Picks', version:1, event_key:'2026old', live_mode:true, slots:[{team_key:'frc254',status:'available',tier:'first',picked_by_alliance:null,notes:'',dnp_reason:''}] };
vi.mock('../api', async original => ({ ...(await original<typeof import('../api')>()), getEventTeamsIntel: vi.fn(async () => ({teams:[{team_key:'frc254',team_number:254}]})), listPitEntries: vi.fn(async () => ({entries:[]})), listPicklists: vi.fn(async () => ({picklists:[picklist]})), getPicklist: vi.fn(async () => ({picklist})) }));
vi.mock('../hooks/useSingleFlightPolling', () => ({ useSingleFlightPolling: () => ({triggerNow:vi.fn()}) }));
vi.mock('../hooks/useOnlineStatus', () => ({useOnlineStatus: () => ({online:true})}));
vi.mock('../features/picklists/usePicklistEditor', () => ({ usePicklistEditor: () => {
  const [doc,setDoc] = useState<Picklist|null>(null);
  const select = useCallback((value: Picklist|null) => setDoc(value),[]);
  const edit = (change: (value: Picklist) => Picklist) => { setDoc(previous => { if (!previous) return previous; const next = change(previous); saved.edit(next); return next; }); };
  return {doc,select,edit,saving:false,pending:false};
} }));
it('removes old-event lists immediately and offers retry after a new-event failure', async () => {
  const { listPicklists } = await import('../api');
  let reject!: (error: Error) => void;
  const pending = new Promise<Awaited<ReturnType<typeof listPicklists>>>((_resolve, fail) => { reject = fail; });
  vi.mocked(listPicklists).mockImplementation((key) => key === '2026old' ? Promise.resolve({picklists:[picklist]} as never) : pending);
  render(<MemoryRouter initialEntries={['/?event=2026old']}><PicklistPage /></MemoryRouter>);
  await screen.findByRole('button', { name: 'Picked' });
  fireEvent.click(screen.getByText('New event'));
  expect(screen.queryByRole('combobox', { name: 'Select picklist' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Picked' })).not.toBeInTheDocument();
  await act(async () => reject(new Error('offline')));
  await screen.findByText(/Could not load picklists/);
  expect(screen.queryByText('No picklist yet')).not.toBeInTheDocument();
  vi.mocked(listPicklists).mockResolvedValue({picklists:[]} as never);
  fireEvent.click(screen.getByRole('button', { name: 'Retry picklists' }));
  await screen.findByText('No picklist yet');
  expect(screen.queryByRole('combobox', { name: 'Select picklist' })).not.toBeInTheDocument();
});
it('explains and retries failed team loading before enabling team actions', async () => {
  const { getEventTeamsIntel, listPicklists } = await import('../api');
  vi.mocked(listPicklists).mockResolvedValue({picklists:[picklist]} as never);
  vi.mocked(getEventTeamsIntel).mockRejectedValueOnce(new Error('offline'));
  render(<MemoryRouter initialEntries={['/?event=2026old']}><PicklistPage /></MemoryRouter>);
  await screen.findByText(/Could not load event teams/);
  expect(screen.getByRole('button', { name: 'New from ratings' })).toBeDisabled();
  expect(await screen.findByRole('button', { name: 'Add missing teams' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Retry teams' }));
  await waitFor(() => expect(screen.getByRole('button', { name: 'Add missing teams' })).toBeEnabled());
});
