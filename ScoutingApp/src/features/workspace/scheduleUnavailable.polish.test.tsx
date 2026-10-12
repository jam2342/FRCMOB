import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import { ScoutingAssignPage } from '../../pages/ScoutingAssignPage';
import { TeamScoutingCard } from './TeamScoutingCard';
const room = vi.hoisted(() => ({
  schedule: null,
  scheduleError: 'Could not load the match schedule.',
  scheduleLoading: false,
  eventName: 'Week 0',
  status: 'ready',
  snapshot: { me: {member_id:1,role:'leader'}, members:[], assignments:[] },
  isLeader: true,
  online: true,
  fromSavedCopy: false,
  error: '',
  retry: vi.fn(),
  saveChanges: vi.fn(),
}));
vi.mock('./useTeamRoom', () => ({useTeamRoom: () => room}));
vi.mock('./WorkspaceGate', () => ({WorkspaceGate: ({children}: {children:React.ReactNode}) => children}));
vi.mock('../../components/EventPicker', () => ({EventPicker: () => null}));
vi.mock('../../components/PageViewBar', () => ({PageViewBar: () => null}));
afterEach(() => {cleanup(); localStorage.clear(); room.isLeader=true; room.retry.mockClear();});
it('withholds assignment empty states and coverage totals until the schedule is available', () => {
  render(<MemoryRouter initialEntries={['/?event=2026week0']}><ScoutingAssignPage /></MemoryRouter>);
  expect(screen.getByRole('alert')).toHaveTextContent('Could not load the match schedule.');
  expect(screen.queryByText('Slots assigned')).not.toBeInTheDocument();
  expect(screen.queryByText(/No schedule published|No matches assigned/)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', {name:'Retry schedule'}));
  expect(room.retry).toHaveBeenCalledTimes(1);
});
it('withholds My Team empty-assignment claims when its schedule failed', () => {
  room.isLeader=false;
  render(<MemoryRouter initialEntries={['/?event=2026week0']}><TeamScoutingCard /></MemoryRouter>);
  expect(screen.getByRole('alert')).toHaveTextContent('Could not load the match schedule.');
  expect(screen.queryByText(/No upcoming matches|No matches assigned/)).not.toBeInTheDocument();
});
