import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
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
it('marks a team Picked without assigning an invented alliance', async () => {
  render(<MemoryRouter initialEntries={['/?event=2026old']}><PicklistPage /></MemoryRouter>);
  fireEvent.click(await screen.findByRole('button',{name:'Picked'}));
  await waitFor(() => expect(saved.edit).toHaveBeenCalled());
  const change = saved.edit.mock.calls.at(-1)![0];
  expect(change.slots[0].status).toBe('picked');
  expect(change.slots[0].picked_by_alliance).toBeNull();
  expect(screen.getByText('Picked')).toBeInTheDocument();
  expect(screen.queryByText('A1')).not.toBeInTheDocument();
});
