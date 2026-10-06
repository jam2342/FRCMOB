import { cleanup, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import { StrategyBriefingPage } from './StrategyBriefingPage';
vi.mock('../api', () => ({
  getEventSchedule: vi.fn(async () => ({ matches: [2,1].map(number => ({
    match_key:`2026old_qm${number}`,display_name:`Qual ${number}`,comp_level:'qm',set_number:1,match_number:number,is_completed:false,
    red:[{team_key:'frc118',team_number:118}],blue:[{team_key:'frc254',team_number:254}],
  })) })),
  getEventScheduleWithSynergy: vi.fn(async () => ({matches:[]})),
  getEventRatings: vi.fn(async () => ({ratings:[]})),
  getEventTeamLiveForm: vi.fn(async () => ({team_statuses:{}})),
}));
vi.mock('../components/EventPicker', () => ({EventPicker: () => null}));
vi.mock('../components/PageViewBar', () => ({PageViewBar: () => null}));
vi.mock('../components/LiveRatingsPanel', () => ({LiveRatingsPanel: () => <p>Ratings content</p>}));
vi.mock('../features/workspace/myTeam', () => ({readMyTeamKey: () => 'frc118',saveMyTeamKey: vi.fn(),workspaceTeamKey: () => 'frc118'}));
afterEach(() => {cleanup();localStorage.clear();vi.unstubAllGlobals();});
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
