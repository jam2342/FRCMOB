import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { getEventMatchup } from '../../api';
import { CustomMatchup } from './CustomMatchup';

vi.mock('../../api', () => ({ getEventMatchup: vi.fn() }));

const TEAMS = ['frc254', 'frc1678', 'frc118', 'frc2056', 'frc971', 'frc4414', 'frc33'];

function choose(label: string, teamKey: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value: teamKey } });
}

function fillSix() {
  choose('Red 1', 'frc254'); choose('Red 2', 'frc1678'); choose('Red 3', 'frc118');
  choose('Blue 1', 'frc2056'); choose('Blue 2', 'frc971'); choose('Blue 3', 'frc4414');
}

afterEach(() => vi.mocked(getEventMatchup).mockReset());

describe('CustomMatchup', () => {
  it('asks only once all six are chosen, then shows expected scores and the call', async () => {
    vi.mocked(getEventMatchup).mockResolvedValue({
      ok: true, event_key: '2026cmptx', red: [], blue: [], red_points: 141.6, blue_points: 120.2, red_win_prob: 0.78, blue_win_prob: 0.22, matches_seen: 1,
    });
    render(<CustomMatchup eventKey="2026cmptx" teamKeys={TEAMS} />);
    expect(screen.getByText('Choose all six teams to see the prediction.')).toBeInTheDocument();
    choose('Red 1', 'frc254');
    expect(getEventMatchup).not.toHaveBeenCalled();
    fillSix();
    expect(await screen.findByText('Red favoured · 78% to win')).toBeInTheDocument();
    expect(getEventMatchup).toHaveBeenLastCalledWith('2026cmptx', ['frc254', 'frc1678', 'frc118'], ['frc2056', 'frc971', 'frc4414'], false);
    expect(screen.getByText('142')).toBeInTheDocument();
    expect(screen.getByRole('img', { name: 'Red 78%, blue 22%' })).toBeInTheDocument();
    expect(screen.getByText(/fewer than three matches here/)).toBeInTheDocument();
  });

  it('never offers a team twice, and asks again for a playoff or a swap', async () => {
    vi.mocked(getEventMatchup).mockResolvedValue({ ok: true, event_key: 'e', red: [], blue: [], red_points: 90, blue_points: 90, red_win_prob: 0.5, blue_win_prob: 0.5, matches_seen: 5 });
    render(<CustomMatchup eventKey="2026cmptx" teamKeys={TEAMS} />);
    choose('Red 1', 'frc254');
    const blueOne = screen.getByLabelText('Blue 1');
    expect([...blueOne.querySelectorAll('option')].map((o) => o.getAttribute('value'))).not.toContain('frc254');
    fillSix();
    expect(await screen.findByText('About even')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('switch', { name: 'Playoff match' }));
    await vi.waitFor(() => expect(getEventMatchup).toHaveBeenLastCalledWith('2026cmptx', expect.any(Array), expect.any(Array), true));
    fireEvent.click(screen.getByRole('button', { name: 'Swap sides' }));
    await vi.waitFor(() => expect(getEventMatchup).toHaveBeenLastCalledWith('2026cmptx', ['frc2056', 'frc971', 'frc4414'], ['frc254', 'frc1678', 'frc118'], true));
  });

  it('explains a past season instead of guessing', async () => {
    vi.mocked(getEventMatchup).mockResolvedValue({ ok: true, event_key: 'e', red: [], blue: [], unavailable: 'season_frozen' });
    render(<CustomMatchup eventKey="2026cmptx" teamKeys={TEAMS} />);
    fillSix();
    expect(await screen.findByText(/This season is finished/)).toBeInTheDocument();
  });
});
