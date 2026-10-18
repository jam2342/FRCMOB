import { render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { CountUp } from './CountUp';

it('names the final value for screen readers and says N/A when there is none', () => {
  const { rerender } = render(<CountUp value={88.44} digits={1} />);
  expect(screen.getByLabelText('88.4')).toBeInTheDocument();
  rerender(<CountUp value={null} />);
  expect(screen.getByText('N/A')).toBeInTheDocument();
});

it('never flashes the final value before counting up when the number arrives late', () => {
  vi.stubGlobal('matchMedia', () => ({ matches: false }));
  const { rerender, container } = render(<CountUp value={null} digits={1} />);
  rerender(<CountUp value={68.7} digits={1} />);
  // Same commit as the value arriving: the count starts at zero.
  expect(container.querySelector('[aria-hidden="true"]')?.textContent).toBe('0.0');
  vi.unstubAllGlobals();
});
