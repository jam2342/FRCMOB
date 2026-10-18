import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { StrategyBriefingPage } from './StrategyBriefingPage';
import { getEventSchedule, getEventPredictionSchedule, getEventRatings, getEventTeamLiveForm } from '../api';
vi.mock('../hooks/useLiveRefreshSetting', () => ({ useLiveRefreshSetting: () => 10 }));
vi.mock('../api', () => ({
  getEventSchedule: vi.fn(async () => ({ matches: [2,1].map(number => ({
    match_key:`2026old_qm${number}`,display_name:`Qual ${number}`,comp_level:'qm',set_number:1,match_number:number,is_completed:false,
    red:[{team_key:'frc118',team_number:118}],blue:[{team_key:'frc254',team_number:254}],
  })) })),
  getEventPredictionSchedule: vi.fn(async () => ({matches:[]})),
  getEventRatings: vi.fn(async () => ({ratings:[]})),
  getEventTeamLiveForm: vi.fn(async () => ({team_statuses:{}})),
}));
vi.mock('../components/EventPicker', () => ({ EventPicker: ({ onSelect }: { onSelect: (key: string) => void }) => <button onClick={() => onSelect('2026new')}>New event</button> }));
vi.mock('../components/PageViewBar', () => ({PageViewBar: () => null}));
vi.mock('../components/LiveRatingsPanel', () => ({LiveRatingsPanel: () => <p>Ratings content</p>}));
vi.mock('../features/workspace/myTeam', () => ({readMyTeamKey: () => 'frc118',saveMyTeamKey: vi.fn(),workspaceTeamKey: () => 'frc118'}));
beforeEach(() => {
  vi.mocked(getEventSchedule).mockResolvedValue({ matches: testMatches() } as never);
  Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
});
afterEach(() => {cleanup();localStorage.clear();vi.unstubAllGlobals();vi.useRealTimers();vi.clearAllMocks();});
function testMatches(completed = false, withFinals = completed) {
  const quals = [2, 1].map(number => ({
    match_key: `2026old_qm${number}`, display_name: `Qual ${number}`, comp_level: 'qm', set_number: 1, match_number: number, is_completed: completed,
    red: [{ team_key: 'frc118', team_number: 118 }], blue: [{ team_key: 'frc254', team_number: 254 }],
  }));
  // Only finished finals end an event; without them the page keeps refreshing.
  return withFinals
    ? [...quals, { ...quals[0], match_key: '2026old_f1m1', display_name: 'Final 1', comp_level: 'f', match_number: 1 }]
    : quals;
}
it.each([false,true])('shows next briefing first, before ratings, and phone print visibility (phone=%s)', async phone => {
  vi.stubGlobal('matchMedia', () => ({matches:phone,addEventListener:vi.fn(),removeEventListener:vi.fn()}));
  render(<MemoryRouter initialEntries={['/?event=2026old']}><StrategyBriefingPage /></MemoryRouter>);
  const first = await screen.findByRole('heading',{name:'Qual 1'});
  const second = screen.getByRole('heading',{name:'Qual 2'});
  const ratings = screen.getByRole('heading',{name:'Live Ratings'});
  expect(first.compareDocumentPosition(second) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(second.compareDocumentPosition(ratings) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  if (phone) expect(screen.queryByRole('button',{name:'Print / PDF'})).not.toBeInTheDocument();
  else expect(screen.getByRole('button',{name:'Print / PDF'})).toBeInTheDocument();
});

it('keeps refreshing between stages until the finals are done', async () => {
  vi.useFakeTimers();
  vi.mocked(getEventSchedule).mockResolvedValue({ matches: testMatches(true, false) } as never);
  render(<MemoryRouter initialEntries={['/?event=2026old']}><StrategyBriefingPage /></MemoryRouter>);
  await act(async () => {});
  expect(screen.getByText('No upcoming matches published yet.')).toBeInTheDocument();
  const calls = vi.mocked(getEventSchedule).mock.calls.length;
  await act(async () => { vi.advanceTimersByTime(60_000); });
  expect(vi.mocked(getEventSchedule).mock.calls.length).toBeGreaterThan(calls);
});

it('refreshes every input, pauses while hidden, resumes on return, and stops after completion', async () => {
  vi.useFakeTimers();
  render(<MemoryRouter initialEntries={['/?event=2026old']}><StrategyBriefingPage /></MemoryRouter>);
  await act(async () => {});
  expect(screen.getByRole('heading', { name: 'Qual 1' })).toBeInTheDocument();
  await act(async () => { vi.advanceTimersByTime(10_000); });
  for (const request of [getEventSchedule, getEventPredictionSchedule, getEventRatings, getEventTeamLiveForm]) {
    expect(request).toHaveBeenCalledTimes(2);
  }
  expect(getEventSchedule).toHaveBeenLastCalledWith('2026old', false, undefined, { bypassCache: true });
  // Polls skip the cache but never ask the server to rebuild: ratings refresh is admin-only.
  expect(getEventRatings).toHaveBeenLastCalledWith('2026old', false, { bypassCache: true });
  expect(getEventPredictionSchedule).toHaveBeenLastCalledWith('2026old', { bypassCache: true });
  act(() => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' });
    document.dispatchEvent(new Event('visibilitychange'));
  });
  await act(async () => { vi.advanceTimersByTime(30_000); });
  expect(getEventSchedule).toHaveBeenCalledTimes(2);
  vi.mocked(getEventSchedule).mockResolvedValue({ matches: testMatches(true) } as never);
  await act(async () => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
    document.dispatchEvent(new Event('visibilitychange'));
  });
  expect(getEventSchedule).toHaveBeenCalledTimes(3);
  expect(screen.queryByRole('heading', { name: 'Qual 1' })).not.toBeInTheDocument();
  await act(async () => { vi.advanceTimersByTime(30_000); });
  expect(getEventSchedule).toHaveBeenCalledTimes(3);
  vi.mocked(getEventSchedule).mockResolvedValue({ matches: testMatches() } as never);
  fireEvent.click(screen.getByRole('button', { name: 'New event' }));
  await act(async () => {});
  expect(getEventSchedule).toHaveBeenCalledTimes(4);
  expect(getEventSchedule).toHaveBeenLastCalledWith('2026new', false, undefined, { bypassCache: true });
});
