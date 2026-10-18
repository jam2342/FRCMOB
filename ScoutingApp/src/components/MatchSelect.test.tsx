import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { EventScheduleItem } from '../api';
import { MatchSelect } from './MatchSelect';

const match = (code: string, level: string, set: number, number: number, display: string): EventScheduleItem => ({
  match_key: `2026test_${code}`, display_name: display, comp_level: level, set_number: set, match_number: number,
  scheduled_time: null, has_time: false, red: [], blue: [],
});
const matches = [
  ...[5, 12, 50, 112, 120].map((n) => match(`qm${n}`, 'qm', 1, n, `QM ${n}`)),
  match('sf1m2', 'sf', 1, 2, 'SF 1-2'),
];
const asPhone = (phone: boolean) => vi.stubGlobal('matchMedia', () => ({ matches: phone, addEventListener: vi.fn(), removeEventListener: vi.fn() }));

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it('on phones, finds a match by the start of its number and picks it with one tap', () => {
  asPhone(true);
  const onChange = vi.fn();
  render(<MatchSelect matches={matches} value="" onChange={onChange} />);
  fireEvent.change(screen.getByLabelText('Find match'), { target: { value: '12' } });
  const group = screen.getByRole('group', { name: 'Match' });
  expect(within(group).getAllByRole('button').map((b) => b.textContent)).toEqual(['QM 12', 'QM 120']);
  fireEvent.click(within(group).getByRole('button', { name: 'QM 12' }));
  expect(onChange).toHaveBeenCalledWith('2026test_qm12');
  fireEvent.change(screen.getByLabelText('Find match'), { target: { value: 'sf' } });
  expect(within(group).getAllByRole('button').map((b) => b.textContent)).toEqual(['SF 1-2']);
});

it('offers "any match" first when it filters, and keeps a match chosen before the schedule arrived', () => {
  asPhone(true);
  const onChange = vi.fn();
  render(<MatchSelect matches={[]} value="2026test_qm7" onChange={onChange} anyLabel="Any match" />);
  const buttons = within(screen.getByRole('group', { name: 'Match' })).getAllByRole('button');
  expect(buttons.map((b) => b.textContent)).toEqual(['Any match', 'QM 7']);
  expect(buttons[1]).toHaveAttribute('aria-pressed', 'true');
  fireEvent.click(buttons[0]);
  expect(onChange).toHaveBeenCalledWith('');
});

it('on desktop, is one dropdown with qualifications and playoffs grouped', () => {
  asPhone(false);
  render(<MatchSelect matches={matches} value="2026test_sf1m2" onChange={vi.fn()} />);
  const select = screen.getByLabelText('Match') as HTMLSelectElement;
  expect(select.value).toBe('2026test_sf1m2');
  expect([...select.querySelectorAll('optgroup')].map((g) => g.label)).toEqual(['Qualifications', 'Playoffs']);
});
