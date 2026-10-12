import { cleanup, fireEvent, render, screen, } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
vi.mock('../hooks/useMobileLayout', () => ({ MOBILE_LAYOUT_BREAKPOINT: 760, useMobileLayout: () => false }));
vi.mock('../components/PageViewBar', () => ({ PageViewBar: () => null }));
vi.mock('../components/EventPicker', () => ({ EventPicker: ({ onSelect, value }: { onSelect: (key: string) => void; value: string }) => <>
  <button onClick={() => onSelect('2026new')}>New event</button>
  <button onClick={() => onSelect(value)}>Load event</button>
</> }));
afterEach(() => { cleanup(); localStorage.clear(); vi.restoreAllMocks(); });
import { AutoPathPage } from './AutoPathPage';
import { getTeamBreakdown } from '../api';
vi.mock('../api', () => ({ getTeamBreakdown: vi.fn(), getEventSchedule: vi.fn(async () => ({event_name:'Test event',matches:[]})) }));
vi.mock('../hooks/useCanvasTokens', () => ({ useCanvasTokens: () => ({}) }));

it('keeps persistent accessible labels for team, match and path name', async () => {
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null);
  vi.mocked(getTeamBreakdown).mockResolvedValue({ok:true} as never);
  render(<MemoryRouter initialEntries={['/?event=2026old&team=254']}><AutoPathPage /></MemoryRouter>);
  expect(screen.getByLabelText('Team')).toHaveValue('254');
  expect(screen.getByLabelText('Match')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', {name:'Draw'}));
  const path = screen.getByLabelText('Path name');
  fireEvent.change(path, {target:{value:'Left side'}});
  expect(screen.getByLabelText('Path name')).toHaveValue('Left side');
});
