import { render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { CountUp } from './CountUp';

it('names the final value for screen readers and says — when there is none', () => {
  const { rerender, container } = render(<CountUp value={88.44} digits={1} />);
  // Read once as text (a plain span's aria-label isn't reliably read); the counting digits are hidden.
  const spoken = [...container.querySelectorAll('span')].filter((el) => el.textContent === '88.4' && !el.closest('[aria-hidden="true"]'));
  expect(spoken).toHaveLength(1);
  expect(container.querySelector('[aria-label]')).toBeNull();
  rerender(<CountUp value={null} />);
  expect(screen.getByText('—')).toBeInTheDocument();
});

it('never flashes the final value before counting up when the number arrives late', () => {
  vi.stubGlobal('matchMedia', () => ({ matches: false }));
  const { rerender, container } = render(<CountUp value={null} digits={1} />);
  rerender(<CountUp value={68.7} digits={1} />);
  // Same commit as the value arriving: the count starts at zero.
  expect(container.querySelector('[aria-hidden="true"]')?.textContent).toBe('0.0');
  vi.unstubAllGlobals();
});
