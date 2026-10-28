import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';

vi.mock('../../api', () => ({ searchTeams: vi.fn(async () => ({ teams: [] })) }));
vi.mock('../../utils/eventSearchIndex', () => ({
  eventDisplayName: (key: string) => key.toUpperCase(),
  getEventSearchIndexSnapshot: () => [{ event_key: '2026arc', name: 'Archimedes Division', year: 2026, city: 'Houston' }],
}));
vi.mock('../workspace/myTeam', () => ({ readMyTeamKey: () => 'frc1678' }));

import { CommandPalette } from './CommandPalette';

function Where() {
  const location = useLocation();
  return <p>at {location.pathname}{location.search}</p>;
}

afterEach(cleanup);

function mount(onClose = vi.fn(), { tour = true } = {}) {
  render(
    <MemoryRouter initialEntries={['/home']}>
      <Routes><Route path="*" element={<Where />} /></Routes>
      <CommandPalette open onClose={onClose} recentEvents={[]} recentTeams={[]} onStartTour={tour ? vi.fn() : undefined} />
    </MemoryRouter>,
  );
  return onClose;
}

it('offers the page tour only on pages that have one', () => {
  mount();
  fireEvent.change(screen.getByRole('combobox'), { target: { value: 'tour' } });
  expect(screen.getByRole('option', { name: /Take the tour of this page/ })).toBeInTheDocument();
  cleanup();
  mount(vi.fn(), { tour: false });
  fireEvent.change(screen.getByRole('combobox'), { target: { value: 'tour' } });
  expect(screen.queryByRole('option', { name: /Take the tour of this page/ })).toBeNull();
});

it('moves with the arrow keys and opens the highlighted result with Enter', () => {
  const onClose = mount();
  const input = screen.getByRole('combobox');
  fireEvent.change(input, { target: { value: 'arch' } });
  expect(screen.getByRole('option', { name: /Archimedes Division/ })).toHaveAttribute('aria-selected', 'true');
  fireEvent.keyDown(input, { key: 'Enter' });
  expect(onClose).toHaveBeenCalled();
  expect(screen.getByText('at /events?event=2026arc')).toBeInTheDocument();
});

it('offers my team as an action, and closes on Escape', () => {
  const onClose = mount();
  const input = screen.getByRole('combobox');
  fireEvent.change(input, { target: { value: 'my team' } });
  expect(screen.getByRole('option', { name: /Open my team \(1678\)/ })).toBeInTheDocument();
  fireEvent.keyDown(input, { key: 'ArrowDown' });
  fireEvent.keyDown(input, { key: 'Escape' });
  expect(onClose).toHaveBeenCalled();
});

it('says so when nothing matches', () => {
  mount();
  fireEvent.change(screen.getByRole('combobox'), { target: { value: 'zzzz' } });
  expect(screen.getByText(/Nothing matches “zzzz”/)).toBeInTheDocument();
});
