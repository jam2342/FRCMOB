import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { PicklistSlot } from '../../api';
import { DraftBoard } from './DraftBoard';
import { draftFromSlots, seedCaptains } from './draft';

vi.mock('../../api', () => ({ getPickValues: vi.fn(async () => ({ ok: true, candidates: [] })) }));

const team = (n: number) => `frc${n}`;
const RANKED = Array.from({ length: 12 }, (_, i) => team(i + 1));
const slot = (n: number, tier: PicklistSlot['tier'] = 'first'): PicklistSlot => ({ team_key: team(n), tier, status: 'available', picked_by_alliance: null, dnp_reason: '', notes: '' });
const LIST = [slot(11), slot(12), slot(9), slot(10), slot(7, 'dnp')];

function board(slots: PicklistSlot[], ranked: string[] | null = RANKED, ourTeamKey: string | null = null) {
  const onApply = vi.fn();
  render(
    <DraftBoard
      eventKey="2026mndu"
      slots={slots}
      ranked={ranked}
      eventTeams={RANKED}
      teamInfo={new Map([[team(11), { nickname: 'Eleven', rating_0_100: 81 }]])}
      ourTeamKey={ourTeamKey}
      onApply={onApply}
      onOpenTeam={vi.fn()}
    />,
  );
  return onApply;
}

describe('DraftBoard', () => {
  it('starts by setting captains from the rankings', () => {
    const onApply = board(LIST);
    fireEvent.click(screen.getByRole('button', { name: 'Set captains from rankings' }));
    const [next, message] = onApply.mock.calls[0];
    expect(message).toBe('Captains set from the rankings');
    expect(draftFromSlots(next).alliances.map((a) => a.captain)).toEqual(RANKED.slice(0, 8));
  });

  it('says so when captains come from the results because TBA has no rankings', () => {
    render(
      <DraftBoard eventKey="e" slots={LIST} ranked={RANKED} rankedFromResults eventTeams={RANKED} teamInfo={new Map()} ourTeamKey={null} onApply={vi.fn()} onOpenTeam={vi.fn()} />,
    );
    expect(screen.getByText(/TBA has no rankings for this event yet/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Set captains from rankings' })).toBeEnabled();
  });

  it("can't start without rankings", () => {
    board(LIST, null);
    expect(screen.getByRole('button', { name: 'Set captains from rankings' })).toBeDisabled();
    expect(screen.getByText(/needs the event rankings/)).toBeInTheDocument();
  });

  it('shows who is on the clock and records a pick from our best available', () => {
    const onApply = board(seedCaptains(LIST, RANKED), RANKED, team(1));
    expect(screen.getByText('Alliance 1 is on the clock · round 1')).toBeInTheDocument();
    const alliances = screen.getByRole('list', { name: 'Alliances' });
    expect(within(alliances).getByRole('listitem', { name: 'Alliance 1, on the clock, our alliance' })).toHaveTextContent('#1');
    const pickSection = screen.getByRole('region', { name: 'Pick for alliance 1' });
    // Our order, DNP left out, nickname and rating shown.
    expect(within(pickSection).getAllByRole('listitem').map((li) => li.textContent?.match(/#\d+/)?.[0])).toEqual(['#11', '#12', '#9', '#10', '#2']);
    expect(within(pickSection).getByText('Eleven')).toBeInTheDocument();
    fireEvent.click(within(pickSection).getAllByRole('button', { name: 'Picked' })[0]);
    const [next, message] = onApply.mock.calls[0];
    expect(message).toBe('Alliance 1 picked #11');
    expect(draftFromSlots(next).alliances[0].picks).toEqual([team(11)]);
  });

  it('records a pick of a team our list left out', () => {
    const onApply = board(seedCaptains(LIST, RANKED));
    fireEvent.change(screen.getByLabelText('Someone else'), { target: { value: team(12) } });
    fireEvent.click(screen.getByRole('button', { name: 'Record pick' }));
    expect(draftFromSlots(onApply.mock.calls[0][0]).alliances[0].picks).toEqual([team(12)]);
  });

  it('records a decline without using up the pick', () => {
    const onApply = board(seedCaptains(LIST, RANKED));
    fireEvent.click(within(screen.getByRole('region', { name: 'Pick for alliance 1' })).getAllByRole('button', { name: 'Declined' })[0]);
    const [next, message] = onApply.mock.calls[0];
    expect(message).toBe('#11 declined');
    expect(draftFromSlots(next).onClock).toEqual({ alliance: 1, round: 1 });
  });

  it('keeps a captain who declines on their alliance and out of the picks', () => {
    // Nothing on our list, so best available is the captains below plus the rest of the field.
    const onApply = board(seedCaptains([], RANKED));
    const pickSection = screen.getByRole('region', { name: 'Pick for alliance 1' });
    expect(within(pickSection).getAllByRole('listitem')[0]).toHaveTextContent('#2A2 captain');
    fireEvent.click(within(pickSection).getAllByRole('button', { name: 'Declined' })[0]);
    expect(onApply).not.toHaveBeenCalled();
    expect(screen.getByText('Alliance 1 is on the clock · round 1')).toBeInTheDocument();
    const rows = within(screen.getByRole('region', { name: 'Pick for alliance 1' })).getAllByRole('listitem');
    expect(rows.map((li) => li.textContent?.match(/#\d+/)?.[0])).not.toContain('#2');
    expect(within(screen.getByLabelText('Someone else')).queryByRole('option', { name: /^#2\b/ })).toBeNull();
  });

  it('asks before setting captains over picks already on the board', () => {
    // Alliance 8's captain gone (an older board): picks are kept until someone confirms.
    const slots = seedCaptains(LIST, RANKED).map((s): PicklistSlot => {
      if (s.team_key === team(8)) return { ...s, status: 'available', picked_by_alliance: null };
      if (s.team_key === team(12)) return { ...s, status: 'picked', picked_by_alliance: 1 };
      return s;
    });
    const onApply = board(slots);
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    fireEvent.click(screen.getByRole('button', { name: 'Set captains from rankings' }));
    expect(confirm).toHaveBeenCalled();
    expect(onApply).not.toHaveBeenCalled();
    confirm.mockRestore();
  });
});
