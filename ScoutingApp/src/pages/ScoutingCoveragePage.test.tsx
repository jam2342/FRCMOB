import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import { ScoutingCoveragePage } from './ScoutingCoveragePage';
import { getScoutingCoverage } from '../api';
vi.mock('../api', () => ({getScoutingCoverage:vi.fn()}));
vi.mock('../features/workspace/WorkspaceGate', () => ({WorkspaceGate: ({children}:{children:React.ReactNode}) => children}));
vi.mock('../components/EventPicker', () => ({EventPicker: ({value,onSelect}:{value:string;onSelect:(key:string)=>void}) => <button onClick={() => onSelect(value)}>Load event</button>}));
afterEach(() => {cleanup(); localStorage.clear();});
it('retries the same event when Load event is clicked', async () => {
  vi.mocked(getScoutingCoverage).mockRejectedValue(new Error('Could not load coverage'));
  render(<MemoryRouter initialEntries={['/?event=2026test']}><ScoutingCoveragePage /></MemoryRouter>);
  await screen.findByText('Could not load coverage');
  fireEvent.click(screen.getByText('Load event'));
  await waitFor(() => expect(getScoutingCoverage).toHaveBeenCalledTimes(2));
});
