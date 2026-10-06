import { act, renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useAutoScoutDraft } from './useAutoScoutDraft';
import { EMPTY_FORM } from '../pages/scoutingPage.helpers';

const api = vi.hoisted(() => ({ getAutoScoutDraft: vi.fn() }));
vi.mock('../api', async (original) => ({ ...(await original<typeof import('../api')>()), ...api }));

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe('auto-scout draft polling', () => {
  it('polls only while enabled, visible and generating, without overlapping a slow request', async () => {
    vi.useFakeTimers();
    vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible');
    const draft = { id: 1, draft_version: 1, status: 'generating' };
    let finish!: (value: { draft: typeof draft }) => void;
    api.getAutoScoutDraft
      .mockReset()
      .mockResolvedValueOnce({ draft })
      .mockImplementation(
        () =>
          new Promise((resolve) => {
            finish = resolve;
          }),
      );
    const { rerender, unmount } = renderHook(
      ({ enabled }) =>
        useAutoScoutDraft({
          enabled,
          eventKey: '2026test',
          matchKey: '2026test_qm1',
          teamKey: 'frc1',
          scoutProfile: 'Scout',
          form: EMPTY_FORM,
          notes: '',
          setForm: vi.fn(),
          setNotes: vi.fn(),
        }),
      { initialProps: { enabled: true } },
    );
    await act(async () => {});
    expect(api.getAutoScoutDraft).toHaveBeenCalledTimes(2);
    await act(async () => {
      vi.advanceTimersByTime(12_000);
    });
    expect(api.getAutoScoutDraft).toHaveBeenCalledTimes(2);
    await act(async () => {
      finish({ draft });
    });
    act(() => {
      vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden');
      document.dispatchEvent(new Event('visibilitychange'));
    });
    await act(async () => {
      vi.advanceTimersByTime(12_000);
    });
    expect(api.getAutoScoutDraft).toHaveBeenCalledTimes(2);
    act(() => {
      vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible');
      document.dispatchEvent(new Event('visibilitychange'));
    });
    expect(api.getAutoScoutDraft).toHaveBeenCalledTimes(3);
    await act(async () => {
      finish({ draft });
    });
    rerender({ enabled: false });
    await act(async () => {
      vi.advanceTimersByTime(12_000);
    });
    expect(api.getAutoScoutDraft).toHaveBeenCalledTimes(3);
    unmount();
  });
});
