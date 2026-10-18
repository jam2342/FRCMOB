import { render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { getPickValues } from '../../api';
import { BestNextPick } from './BestNextPick';

vi.mock('../../api', () => ({ getPickValues: vi.fn() }));

it('ranks candidates for the captain and says what the third robot is assumed to be', async () => {
  vi.mocked(getPickValues).mockResolvedValue({
    ok: true, event_key: '2026arc', partners: ['frc254'], assumed_third: 'frc4000', reference: ['frc1', 'frc2', 'frc3'],
    candidates: [
      { team_key: 'frc1678', win_vs_strong_alliance: 0.82, alliance_points: 520, points_per_match: 280, rating_0_100: 96.5, climb_points: 2.1, dud_rate: 0.03, bonus_rp_chances: {} },
    ],
  });
  render(<BestNextPick eventKey="2026arc" partners={['frc254']} onOpenTeam={() => {}} />);
  expect(await screen.findByText('#1678')).toBeInTheDocument();
  expect(screen.getByText('82%')).toBeInTheDocument();
  expect(screen.getByText(/third robot: a typical one, #4000/)).toBeInTheDocument();
  expect(vi.mocked(getPickValues)).toHaveBeenCalledWith('2026arc', ['frc254'], 12);
});

it('asks for a captain first', () => {
  render(<BestNextPick eventKey="2026arc" partners={[]} onOpenTeam={() => {}} />);
  expect(screen.getByText('No captain yet')).toBeInTheDocument();
});

it('explains why a past season has no pick values', async () => {
  vi.mocked(getPickValues).mockResolvedValue({
    ok: true, event_key: '2025arc', partners: [], assumed_third: null, reference: [], candidates: [], unavailable: 'season_not_rated',
  });
  render(<BestNextPick eventKey="2025arc" partners={['frc254']} onOpenTeam={() => {}} />);
  expect(await screen.findByText('No pick values for a past season')).toBeInTheDocument();
  expect(screen.getByText(/FRCMOB ratings start in 2026/)).toBeInTheDocument();
});
