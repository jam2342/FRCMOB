import { act, renderHook } from '@testing-library/react';
import { MemoryRouter, useLocation, useNavigate } from 'react-router-dom';
import { beforeEach, expect, it, vi } from 'vitest';
import type { ReactNode } from 'react';
import { useEventKeyParam } from './useEventKeyParam';

beforeEach(() => { localStorage.clear(); vi.restoreAllMocks(); });
const wrapper = ({ children }: { children: ReactNode }) => <MemoryRouter initialEntries={['/picklist?event=2026slow&team=frc254']}>{children}</MemoryRouter>;
const useTestEvent = () => ({ ...useEventKeyParam('test-event'), navigate: useNavigate(), search: useLocation().search });

it('follows repeated same-page links and Back/Forward without restoring an older event', () => {
  const { result } = renderHook(useTestEvent, { wrapper });
  act(() => result.current.navigate('/picklist?event=2026fast&team=frc118'));
  expect(result.current.eventKey).toBe('2026fast');
  expect(result.current.eventInput).toBe('2026fast');
  act(() => result.current.navigate('/picklist?event=2026slow&team=frc254'));
  expect(result.current.eventKey).toBe('2026slow');
  act(() => result.current.navigate(-1));
  expect(result.current.eventKey).toBe('2026fast');
  act(() => result.current.navigate(1));
  expect(result.current.eventKey).toBe('2026slow');
});

it('retries the same event and preserves unrelated direct-link parameters', () => {
  const { result } = renderHook(useTestEvent, { wrapper });
  act(() => result.current.selectEvent(' 2026SLOW '));
  expect(result.current.fetchTrigger).toBe(1);
  expect(result.current.search).toContain('team=frc254');
  act(() => result.current.selectEvent('2026fast'));
  expect(result.current.fetchTrigger).toBe(2);
  expect(result.current.eventKey).toBe('2026fast');
});

it('still navigates when event preference persistence fails', () => {
  vi.spyOn(window.localStorage, 'setItem').mockImplementation(() => { throw new DOMException('Full', 'QuotaExceededError'); });
  const { result } = renderHook(useTestEvent, { wrapper });
  act(() => result.current.selectEvent('2026fast'));
  expect(result.current.eventKey).toBe('2026fast');
});
