import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

vi.mock('../prefetch/pageRequests', () => ({
  loadTeamCenterIntel: vi.fn(async () => ({
    team: { nickname: 'The Cheesy Poofs' },
    rating: { available: true, rating_0_100: 88.4 },
    analysis: { averages: { fuel_scoring_rate: 171.3, climb_success_prob: 0 } },
  })),
}));

import { TeamPeek } from './TeamPeek';

function Where() {
  const location = useLocation();
  return <p>at {location.pathname}{location.search}</p>;
}

const opened = vi.fn();
function mount() {
  render(
    <MemoryRouter initialEntries={['/events']}>
      <Routes><Route path="*" element={<Where />} /></Routes>
      <button type="button" data-team-peek="frc254" data-team-peek-event="2026cmptx" onClick={opened}>#254</button>
      <TeamPeek />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.useFakeTimers();
  opened.mockClear();
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

it('opens on a mouse hover after a short pause, and links to the team', async () => {
  mount();
  fireEvent.pointerOver(screen.getByText('#254'), { pointerType: 'mouse' });
  expect(screen.queryByRole('dialog')).toBeNull();
  await act(async () => {
    vi.advanceTimersByTime(400);
  });
  expect(screen.getByRole('dialog', { name: 'Team 254 at a glance' })).toHaveTextContent('The Cheesy Poofs');
  expect(screen.getByRole('dialog')).toHaveTextContent('171.3');
  expect(screen.getByRole('dialog')).toHaveTextContent('88.4');
  fireEvent.click(screen.getByRole('button', { name: 'Open team' }));
  expect(screen.getByText('at /team-center?event=2026cmptx&team=frc254')).toBeInTheDocument();
});

it('a quick mouse pass does not open it', async () => {
  mount();
  const target = screen.getByText('#254');
  fireEvent.pointerOver(target, { pointerType: 'mouse' });
  fireEvent.pointerOut(target, { pointerType: 'mouse' });
  await act(async () => {
    vi.advanceTimersByTime(1000);
  });
  expect(screen.queryByRole('dialog')).toBeNull();
});

it('opens on a long press on a phone and swallows the tap that would have navigated', async () => {
  mount();
  const target = screen.getByText('#254');
  fireEvent.pointerDown(target, { pointerType: 'touch', clientX: 10, clientY: 10 });
  await act(async () => {
    vi.advanceTimersByTime(500);
  });
  expect(screen.getByRole('dialog')).toBeInTheDocument();
  fireEvent.pointerUp(target, { pointerType: 'touch' });
  fireEvent.click(target);
  expect(opened).not.toHaveBeenCalled();
  // A normal tap afterwards still works.
  fireEvent.click(target);
  expect(opened).toHaveBeenCalledTimes(1);
});

it('swallows the release click that lands on the sheet under the finger, and only that one', async () => {
  mount();
  const target = screen.getByText('#254');
  fireEvent.pointerDown(target, { pointerType: 'touch', clientX: 10, clientY: 10 });
  await act(async () => {
    vi.advanceTimersByTime(500);
  });
  const sheet = screen.getByRole('dialog');
  const openTeam = screen.getByRole('button', { name: 'Open team' });
  // The sheet now covers where the finger was: lifting it "clicks" Open team.
  fireEvent.pointerUp(sheet, { pointerType: 'touch' });
  fireEvent.click(openTeam);
  expect(screen.getByText('at /events')).toBeInTheDocument();
  expect(screen.getByRole('dialog')).toBeInTheDocument();
  // A real tap on it afterwards still works.
  fireEvent.click(openTeam);
  expect(screen.getByText(/^at \/team-center/)).toBeInTheDocument();
});
