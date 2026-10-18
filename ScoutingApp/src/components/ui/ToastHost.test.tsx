import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ToastHost } from './ToastHost';
import { closeToast, currentToast, showToast } from './toast';

describe('toasts', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    const open = currentToast();
    if (open) closeToast(open.id);
    vi.useRealTimers();
  });

  it('shows the message and closes itself after its duration', () => {
    const onClose = vi.fn();
    render(<ToastHost />);
    act(() => {
      showToast({ message: 'Saved Q32 · 254', durationMs: 3000, onClose });
    });
    expect(screen.getByRole('status')).toHaveTextContent('Saved Q32 · 254');
    act(() => {
      vi.advanceTimersByTime(3000);
    });
    expect(onClose).toHaveBeenCalledWith('timeout');
    act(() => {
      vi.advanceTimersByTime(300);
    });
    expect(screen.queryByRole('status')).toBeNull();
  });

  it('runs the action once and reports the close as the action', () => {
    const onAction = vi.fn();
    const onClose = vi.fn();
    render(<ToastHost />);
    act(() => {
      showToast({ message: 'Deleted list', actionLabel: 'Undo', onAction, onClose });
    });
    fireEvent.click(screen.getByRole('button', { name: 'Undo' }));
    expect(onAction).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalledWith('action');
    act(() => {
      vi.advanceTimersByTime(10000);
    });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('closes a replaced toast as replaced, so held-back work still runs', () => {
    const first = vi.fn();
    render(<ToastHost />);
    act(() => {
      showToast({ message: 'First', actionLabel: 'Undo', onClose: first });
      showToast({ message: 'Second' });
    });
    expect(first).toHaveBeenCalledWith('replaced');
    expect(screen.getByRole('status')).toHaveTextContent('Second');
  });

  it('pauses the countdown while the pointer is on the toast', () => {
    const onClose = vi.fn();
    render(<ToastHost />);
    act(() => {
      showToast({ message: 'Hold', actionLabel: 'Undo', durationMs: 2000, onClose });
    });
    act(() => {
      vi.advanceTimersByTime(1500);
    });
    fireEvent.pointerEnter(screen.getByRole('status'));
    act(() => {
      vi.advanceTimersByTime(5000);
    });
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.pointerLeave(screen.getByRole('status'));
    act(() => {
      vi.advanceTimersByTime(600);
    });
    expect(onClose).toHaveBeenCalledWith('timeout');
  });

  it('ignores a close for a toast that has already been replaced', () => {
    const id = showToast({ message: 'Old' });
    showToast({ message: 'New' });
    closeToast(id);
    expect(currentToast()?.message).toBe('New');
  });
});
