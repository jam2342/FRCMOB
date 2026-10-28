import { fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { getTeamNotes, type TeamScoutNote } from '../../api';
import { ScoutNotesCard } from './ScoutNotesCard';
import { filterTeamNotes } from './noteTags';

vi.mock('../../api', () => ({ getTeamNotes: vi.fn() }));

function note(id: number, text: string, tags: string[] = [], scout = 'Ava'): TeamScoutNote {
  return { id, event_key: '2026mndu', match_key: `2026mndu_qm${id}`, scout_profile: scout, notes: text, tags, saved_at_ms: id, created_at: null };
}

afterEach(() => vi.mocked(getTeamNotes).mockReset());

describe('ScoutNotesCard', () => {
  it('reads back what scouts wrote and filters by tag', async () => {
    vi.mocked(getTeamNotes).mockResolvedValue({
      ok: true, team_key: 'frc3100', event_key: '2026mndu', reports: 4,
      tag_counts: [{ tag: 'defense', count: 2 }, { tag: 'broke_down', count: 1 }],
      notes: [
        note(3, 'Pinned 2052 for most of teleop', ['defense']),
        note(2, '', ['broke_down', 'defense'], 'Ben'),
        note(1, 'Fast intake, slow climb'),
      ],
    });
    render(<ScoutNotesCard teamKey="frc3100" eventKey="2026mndu" />);
    expect(await screen.findByText('Pinned 2052 for most of teleop')).toBeInTheDocument();
    expect(screen.getByText('4 reports from your team · 2 with notes')).toBeInTheDocument();
    expect(getTeamNotes).toHaveBeenCalledWith('frc3100', '2026mndu');

    fireEvent.click(screen.getByRole('button', { name: 'Broke down · 1' }));
    expect(screen.getByRole('button', { name: 'Broke down · 1' })).toHaveAttribute('aria-pressed', 'true');
    const items = screen.getAllByRole('listitem');
    expect(items).toHaveLength(1);
    expect(within(items[0]).getByText(/Ben/)).toBeInTheDocument();
    expect(screen.queryByText('Fast intake, slow climb')).not.toBeInTheDocument();
  });

  it('is not drawn at all when nobody wrote anything', async () => {
    vi.mocked(getTeamNotes).mockResolvedValue({ ok: true, team_key: 'frc1', event_key: null, reports: 3, tag_counts: [], notes: [] });
    const { container } = render(<ScoutNotesCard teamKey="frc1" />);
    await vi.waitFor(() => expect(getTeamNotes).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it('is not drawn when the request fails (no workspace, offline)', async () => {
    vi.mocked(getTeamNotes).mockRejectedValue(new Error('401'));
    const { container } = render(<ScoutNotesCard teamKey="frc1" />);
    await vi.waitFor(() => expect(getTeamNotes).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it('shows five, then the rest on request', async () => {
    const notes = Array.from({ length: 8 }, (_, i) => note(8 - i, `note ${8 - i}`));
    vi.mocked(getTeamNotes).mockResolvedValue({ ok: true, team_key: 'frc1', event_key: null, reports: 8, tag_counts: [], notes });
    render(<ScoutNotesCard teamKey="frc1" />);
    expect(await screen.findByText('note 8')).toBeInTheDocument();
    expect(screen.getAllByRole('listitem')).toHaveLength(5);
    fireEvent.click(screen.getByRole('button', { name: 'Show all 8' }));
    expect(screen.getAllByRole('listitem')).toHaveLength(8);
    fireEvent.change(screen.getByLabelText('Search notes'), { target: { value: 'note 3' } });
    expect(screen.getAllByRole('listitem')).toHaveLength(1);
  });
});

describe('filterTeamNotes', () => {
  it('matches text or scout, case-insensitively', () => {
    const notes = [note(1, 'Great DEFENSE'), note(2, 'quiet', [], 'Defense Dan'), note(3, 'nothing')];
    expect(filterTeamNotes(notes, null, 'defense').map((n) => n.id)).toEqual([1, 2]);
    expect(filterTeamNotes(notes, 'tipped', '')).toEqual([]);
  });
});
