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
import { AutoPathPage } from './AutoPathPage';
import { getTeamBreakdown } from '../api';
vi.mock('../api', () => ({ getTeamBreakdown: vi.fn() }));
vi.mock('../hooks/useCanvasTokens', () => ({ useCanvasTokens: () => ({}) }));
function path(id: string, eventKey?: string, teamKey = 'frc254') { return { id, eventKey, teamKey, matchKey: 'qm1', label: id, color: 'red', points: [], createdAt: 1 }; }
it('reads handoff scope, labels old paths, and clears only visible paths after confirmation', async () => {
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null);
  vi.mocked(getTeamBreakdown).mockResolvedValue({ ok: true, recent_track_points: [
    { field_x: 1, field_y: 1, time_sec: 1, match_key: '2026old_qm1' },
    { field_x: 1, field_y: 1, time_sec: 1, match_key: '2026old_qm2' },
  ] } as never);
  localStorage.setItem('autopath_saved_v1', JSON.stringify([path('visible', '2026old'), path('legacy'), path('other-event', '2026new'), path('other-team', '2026old', 'frc1')]));
  render(<MemoryRouter initialEntries={['/?event=2026old&team=frc254&match=2026old_qm1']}><AutoPathPage /></MemoryRouter>);
  expect(screen.getByPlaceholderText('Team # — e.g. 254')).toHaveValue('254');
  expect(screen.getByPlaceholderText('Match — e.g. qm1')).toHaveValue('qm1');
  expect(screen.queryByText('other-event')).not.toBeInTheDocument();
  expect(screen.getByText(/Unknown event/)).toBeInTheDocument();
  await screen.findByText('1 point from an accepted phone recording');
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
  fireEvent.click(screen.getByText('Clear visible paths'));
  expect(confirm).toHaveBeenCalledWith('Clear 2 visible paths?');
  expect(screen.getByText('visible')).toBeInTheDocument();
  confirm.mockReturnValue(true);
  fireEvent.click(screen.getByText('Clear visible paths'));
  await waitFor(() => expect(JSON.parse(localStorage.getItem('autopath_saved_v1')!).map((p: {id:string}) => p.id)).toEqual(['other-event', 'other-team']));
});
it('clears old tracks on failure and retries tracks and the same event', async () => {
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null);
  vi.mocked(getTeamBreakdown).mockResolvedValueOnce({ ok: true, recent_track_points: [{field_x: 1, field_y: 1, time_sec: 1, match_key: '2026old_qm1'}] } as never).mockRejectedValueOnce(new Error('offline')).mockResolvedValue({ok: true, recent_track_points: []} as never);
  render(<MemoryRouter initialEntries={['/?event=2026old&team=254']}><AutoPathPage /></MemoryRouter>);
  await screen.findByText('Detected Auto Tracks');
  fireEvent.change(screen.getByPlaceholderText('Team # — e.g. 254'), { target: { value: '111' } });
  await screen.findByText('Could not load recorded tracks. Try again.');
  expect(screen.queryByText('Detected Auto Tracks')).not.toBeInTheDocument();
  fireEvent.click(screen.getByText('Retry tracks'));
  await waitFor(() => expect(getTeamBreakdown).toHaveBeenCalledTimes(3));
  fireEvent.click(screen.getByText('Load event'));
  await waitFor(() => expect(getTeamBreakdown).toHaveBeenCalledTimes(4));
});
it('shows storage failure with retry instead of claiming persistence', async () => {
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null);
  vi.mocked(getTeamBreakdown).mockResolvedValue({ok: true} as never);
  const original = localStorage.setItem.bind(localStorage);
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation((key, value) => { if (key === 'autopath_saved_v1') throw new Error('full'); original(key, value); });
  render(<MemoryRouter initialEntries={['/?event=2026old']}><AutoPathPage /></MemoryRouter>);
  await screen.findByText(/These paths could not be saved/);
  expect(screen.getByText('Retry saving')).toBeInTheDocument();
});
it('stores the selected event on a newly drawn path', async () => {
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null);
  vi.mocked(getTeamBreakdown).mockResolvedValue({ok:true} as never);
  const view = render(<MemoryRouter initialEntries={['/?event=2026old&team=254&match=2026old_qm1']}><AutoPathPage /></MemoryRouter>);
  fireEvent.click(screen.getByRole('button',{name:'Draw'}));
  const canvas = view.container.querySelector('canvas')!;
  Object.defineProperty(canvas,'setPointerCapture',{value:vi.fn()});
  Object.defineProperty(canvas,'releasePointerCapture',{value:vi.fn()});
  vi.spyOn(canvas,'getBoundingClientRect').mockReturnValue({left:0,top:0,width:200,height:100} as DOMRect);
  vi.stubGlobal('PointerEvent', MouseEvent);
  fireEvent.pointerDown(canvas,{clientX:10,clientY:10});
  fireEvent.pointerMove(canvas,{clientX:100,clientY:40});
  fireEvent.pointerUp(canvas,{clientX:100,clientY:40});
  vi.unstubAllGlobals();
  await waitFor(() => expect(JSON.parse(localStorage.getItem('autopath_saved_v1')!)).toEqual([expect.objectContaining({eventKey:'2026old',teamKey:'frc254',matchKey:'qm1'})]));
  expect(screen.getByText(/254 - qm1 - 2026old/)).toBeInTheDocument();
});
