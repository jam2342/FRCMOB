import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { ErrorBoundary } from './ErrorBoundary';

afterEach(() => { cleanup(); vi.restoreAllMocks(); });
it('keeps exception details out of the fallback and lets the page retry', () => {
  const log = vi.spyOn(console, 'error').mockImplementation(() => {});
  let broken = true;
  function Page() {
    if (broken) throw new Error('private_property_name');
    return <p>Page ready</p>;
  }
  render(<ErrorBoundary><Page /></ErrorBoundary>);
  expect(screen.getByText('This page could not load. Try again, or reload the app if it keeps happening.')).toBeInTheDocument();
  expect(screen.queryByText(/private_property_name/)).not.toBeInTheDocument();
  expect(log).toHaveBeenCalled();
  broken = false;
  fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
  expect(screen.getByText('Page ready')).toBeInTheDocument();
});

it('clears the error when the page it guards changes', () => {
  vi.spyOn(console, 'error').mockImplementation(() => {});
  function Broken(): never { throw new Error('boom'); }
  const { rerender } = render(<ErrorBoundary label="Strategy" resetKey="/match-center/strategy"><Broken /></ErrorBoundary>);
  expect(screen.getByText('Strategy failed to load')).toBeInTheDocument();
  rerender(<ErrorBoundary label="Compare" resetKey="/compare"><p>Compare ready</p></ErrorBoundary>);
  expect(screen.getByText('Compare ready')).toBeInTheDocument();
});
