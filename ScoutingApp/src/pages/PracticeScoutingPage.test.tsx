import { fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import { getEventSchedule, getMatchPractice } from '../api';
import { PracticeScoutingPage } from './PracticeScoutingPage';

vi.mock('../components/ScoutingViewBar', () => ({ ScoutingViewBar: () => null }));
vi.mock('../components/EventPicker', () => ({ EventPicker: () => null }));
vi.mock('../api', () => ({ getEventSchedule: vi.fn(), getMatchPractice: vi.fn() }));

const team = (key: string) => ({ team_key: key, team_number: Number(key.slice(3)), nickname: null, station: null });

afterEach(() => { vi.mocked(getEventSchedule).mockReset(); vi.mocked(getMatchPractice).mockReset(); });

it('scouts a played robot and checks the report against the official result', async () => {
  vi.mocked(getEventSchedule).mockResolvedValue({
    ok: true, event_key: '2026mndu', event_name: 'Duluth', source: 'test', published: true, times_published: true, count: 2,
    matches: [
      { match_key: '2026mndu_qm7', display_name: 'QM 7', comp_level: 'qm', set_number: 1, match_number: 7, is_completed: true, red: [team('frc1'), team('frc2'), team('frc3')], blue: [team('frc11'), team('frc22'), team('frc33')] },
      { match_key: '2026mndu_qm8', display_name: 'QM 8', comp_level: 'qm', set_number: 1, match_number: 8, is_completed: false, red: [], blue: [] },
    ],
  } as never);
  vi.mocked(getMatchPractice).mockResolvedValue({
    ok: true, match_key: '2026mndu_qm7', event_key: '2026mndu', label: 'QM 7', team_key: 'frc22', alliance: 'blue', played: true, checkable: true,
    video: null, official: { auto_tower_points: 0, endgame_tower_points: 20, alliance_fuel: 40, alliance_score: 120 },
  });
  render(<MemoryRouter initialEntries={['/scouting/practice?event=2026mndu']}><PracticeScoutingPage /></MemoryRouter>);

  const matchSelect = await screen.findByLabelText('Match');
  // Only played matches are offered.
  expect(within(matchSelect).queryByRole('option', { name: 'QM 8' })).not.toBeInTheDocument();
  fireEvent.change(matchSelect, { target: { value: '2026mndu_qm7' } });
  fireEvent.click(screen.getByRole('radio', { name: '22' }));
  expect(await screen.findByText('Scout 22 · QM 7')).toBeInTheDocument();
  expect(getMatchPractice).toHaveBeenCalledWith('2026mndu_qm7', 'frc22');
  expect(screen.getByText(/No video of this match is posted yet/)).toBeInTheDocument();

  fireEvent.change(screen.getByLabelText('Endgame'), { target: { value: 'climb_level_1' } });
  fireEvent.click(screen.getByRole('button', { name: 'Check my report' }));
  expect(screen.getByText('1 of 2 right')).toBeInTheDocument();
  expect(screen.getByText('You: Climbed 1 level · Official: Climbed 2 levels')).toBeInTheDocument();

  fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
  expect(screen.queryByText('1 of 2 right')).not.toBeInTheDocument();
});

it("says so when the season's results can't be checked by robot", async () => {
  vi.mocked(getEventSchedule).mockResolvedValue({
    ok: true, event_key: '2025mnmi', event_name: 'Old', source: 'test', published: true, times_published: true, count: 1,
    matches: [{ match_key: '2025mnmi_qm1', display_name: 'QM 1', comp_level: 'qm', set_number: 1, match_number: 1, is_completed: true, red: [team('frc1')], blue: [team('frc2')] }],
  } as never);
  vi.mocked(getMatchPractice).mockResolvedValue({
    ok: true, match_key: '2025mnmi_qm1', event_key: '2025mnmi', label: 'QM 1', team_key: 'frc1', alliance: 'red', played: true, checkable: false,
    video: { embed_url: 'https://www.youtube.com/embed/abcdefghijk', watch_url: 'w' },
    official: { auto_tower_points: null, endgame_tower_points: null, alliance_fuel: null, alliance_score: 50 },
  });
  render(<MemoryRouter initialEntries={['/scouting/practice?event=2025mnmi']}><PracticeScoutingPage /></MemoryRouter>);
  fireEvent.change(await screen.findByLabelText('Match'), { target: { value: '2025mnmi_qm1' } });
  fireEvent.click(screen.getByRole('radio', { name: '1' }));
  expect(await screen.findByText(/nothing to check against/)).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Check my report' })).not.toBeInTheDocument();
});
