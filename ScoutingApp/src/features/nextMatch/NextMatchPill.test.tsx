import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { EventScheduleItem } from '../../api';

const schedule = vi.hoisted(() => ({ matches: [] as EventScheduleItem[] }));
vi.mock('../../api', () => ({ getEventSchedule: vi.fn(async () => ({ matches: schedule.matches })) }));
vi.mock('../workspace/myTeam', () => ({ readMyTeamKey: () => 'frc1678' }));
vi.mock('../../layout/centerContext', () => ({
  CENTER_CONTEXT_UPDATED_EVENT: 'test:context',
  readStoredCenterContext: () => ({ eventKey: '2026test' }),
}));

import { NextMatchPill } from './NextMatchPill';
import { currentToast, runToastAction } from '../../components/ui/toast';

const inMinutes = (min: number): EventScheduleItem => ({
  match_key: '2026test_qm41', display_name: 'Qual 41', comp_level: 'qm', set_number: 1, match_number: 41,
  scheduled_time: Math.floor((Date.now() + min * 60_000) / 1000), has_time: true,
  red: [{ team_key: 'frc4414', team_number: 4414, nickname: null, station: 'r1' }, { team_key: 'frc1678', team_number: 1678, nickname: null, station: 'r2' }],
  blue: [{ team_key: 'frc254', team_number: 254, nickname: null, station: 'b1' }],
});

const mount = (path = '/home', search = '') => render(
  <MemoryRouter initialEntries={[`${path}${search}`]}><NextMatchPill pathname={path} search={search} /></MemoryRouter>,
);

beforeEach(() => {
  localStorage.clear();
});
afterEach(() => {
  cleanup();
  document.documentElement.style.removeProperty('--next-match-pill-space');
});

it('shows the next match, opens into partners and opponents, and leaves room under the page', async () => {
  schedule.matches = [inMinutes(6)];
  mount();
  const bar = await screen.findByRole('button', { name: /Your next match: Q41 · you're Red 2/ });
  expect(bar).toHaveAttribute('aria-expanded', 'false');
  expect(document.documentElement.style.getPropertyValue('--next-match-pill-space')).not.toBe('');
  fireEvent.click(bar);
  expect(bar).toHaveAttribute('aria-expanded', 'true');
  expect(screen.getByText('4414')).toBeInTheDocument();
  expect(screen.getByText('254')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Open Match Center' })).toBeInTheDocument();
});

it('hides for that match with Undo, and stays off the scouting board and that match page', async () => {
  schedule.matches = [inMinutes(30)];
  const view = mount();
  fireEvent.click(await screen.findByRole('button', { name: /Your next match/ }));
  fireEvent.click(screen.getByRole('button', { name: 'Hide' }));
  expect(screen.queryByRole('button', { name: /Your next match/ })).toBeNull();
  expect(currentToast()?.actionLabel).toBe('Undo');
  act(() => runToastAction(currentToast()!.id));
  expect(await screen.findByRole('button', { name: /Your next match/ })).toBeInTheDocument();
  view.unmount();

  mount('/scouting');
  await act(async () => { await Promise.resolve(); });
  expect(screen.queryByRole('button', { name: /Your next match/ })).toBeNull();
  cleanup();
  mount('/match-center', '?event=2026test&match=2026test_qm41');
  await act(async () => { await Promise.resolve(); });
  expect(screen.queryByRole('button', { name: /Your next match/ })).toBeNull();
});

it('shows nothing when the next match is more than an hour away', async () => {
  schedule.matches = [inMinutes(95)];
  mount();
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });
  expect(screen.queryByRole('button', { name: /Your next match/ })).toBeNull();
});
