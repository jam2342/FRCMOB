import { cleanup, renderHook, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { scoutingMatchLabel } from './scoutDraftDisplay';
import { scoutingTeamLabel } from './scoutingPage.helpers';
import { useEventNames } from '../hooks/useEventNames';

vi.mock('../features/events/eventCatalog', () => ({
  loadSeasonEventCatalog: vi.fn(async () => [{ event_key: '2026arc', name: 'Archimedes Division', year: 2026 }]),
  loadSeasonSearchFallback: vi.fn(async () => []),
}));
afterEach(cleanup);

it('uses readable team and match labels', () => {
  expect(scoutingTeamLabel('FRC254')).toBe('#254');
  expect(scoutingMatchLabel('2026arc_qm12')).toBe('QM 12');
  expect(scoutingMatchLabel('2026arc_sf1m2')).toBe('SF 1-2');
});

it('names events from the shared index and prefers a name the page already has', async () => {
  const { result } = renderHook(() => useEventNames('2026home', 'Home event'));
  expect(result.current('2026home')).toBe('Home event');
  await waitFor(() => expect(result.current('2026arc')).toBe('Archimedes Division'));
  // Unknown events still read as a code, never a raw key.
  expect(result.current('2026zzz')).toBe('2026 ZZZ');
});
