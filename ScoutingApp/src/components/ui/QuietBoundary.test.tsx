import { render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { QuietBoundary } from './QuietBoundary';

function Broken(): never {
  throw new Error('Unable to preload CSS for /assets/NextMatchPill.css');
}

it('leaves out an extra that fails instead of taking the page down', () => {
  vi.spyOn(console, 'error').mockImplementation(() => undefined);
  vi.spyOn(console, 'warn').mockImplementation(() => undefined);
  render(
    <main>
      <p>The page</p>
      <QuietBoundary name="next-match"><Broken /></QuietBoundary>
    </main>,
  );
  expect(screen.getByText('The page')).toBeInTheDocument();
});
