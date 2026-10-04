import { act, fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { MoreSheet } from './MoreSheet';

function MoreSheetHarness() {
  const [open, setOpen] = useState(false);
  return (
    <MemoryRouter>
      <button type="button" onClick={() => setOpen(true)}>Open more</button>
      <MoreSheet open={open} onClose={() => setOpen(false)} />
    </MemoryRouter>
  );
}

describe('MoreSheet', () => {
  afterEach(() => {
    vi.useRealTimers();
    document.body.style.overflow = '';
  });

  it('traps focus, closes on Escape, and restores the opener', () => {
    vi.useFakeTimers();
    render(<MoreSheetHarness />);
    const opener = screen.getByRole('button', { name: 'Open more' });
    opener.focus();
    fireEvent.click(opener);

    const dialog = screen.getByRole('dialog', { name: 'Pages' });
    expect(dialog).toHaveAttribute('aria-modal', 'true');
    expect(screen.getByRole('link', { name: 'My Team' })).toHaveFocus();
    expect(document.body.style.overflow).toBe('hidden');

    const lastLink = screen.getByRole('link', { name: 'Terms of Service' });
    lastLink.focus();
    fireEvent.keyDown(document, { key: 'Tab' });
    expect(screen.getByRole('link', { name: 'My Team' })).toHaveFocus();

    fireEvent.keyDown(document, { key: 'Escape' });
    act(() => vi.advanceTimersByTime(200));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(opener).toHaveFocus();
    expect(document.body.style.overflow).toBe('');
  });
});
