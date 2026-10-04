import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { TrackIdentityList, type IdentityTrack } from './TrackIdentityList';
const summaries: IdentityTrack[] = [
  { trackId: 1, pointCount: 3, startSec: 0, endSec: 1, alliance: 'red', dominantZone: null, suggestedTeam: null },
  { trackId: 2, pointCount: 9, startSec: 2, endSec: 5, alliance: 'blue', dominantZone: 'neutral_zone', suggestedTeam: null },
  { trackId: 3, pointCount: 6, startSec: 6, endSec: 8, alliance: null, dominantZone: null, suggestedTeam: null },
];
const teams = [{ teamKey: 'frc1', alliance: 'red' as const }, { teamKey: 'frc2', alliance: 'blue' as const }];
const props = { summaries, identities: { 2: 'frc2' }, teams, photoUrls: {}, onAssign: vi.fn() };
describe('robot path identification', () => {
  it('prioritizes the most recorded points and reports point-weighted assignment progress', () => {
    render(<TrackIdentityList {...props} />);
    expect(screen.getByText('1 of 3 paths assigned · 50% of recorded points assigned')).toBeInTheDocument();
    expect(screen.getAllByRole('combobox', { name: /Assign track/ }).map(el => el.getAttribute('aria-label'))).toEqual(['Assign track 2 to a team', 'Assign track 3 to a team', 'Assign track 1 to a team']);
  });
  it('filters without changing or discarding assignments, including unknown bumper colour', () => {
    const { rerender } = render(<TrackIdentityList {...props} />);
    fireEvent.click(screen.getByRole('checkbox', { name: 'Only unassigned paths' }));
    expect(screen.queryByRole('combobox', { name: 'Assign track 2 to a team' })).toBeNull();
    fireEvent.change(screen.getByRole('combobox', { name: 'Filter paths by bumper colour' }), { target: { value: 'unknown' } });
    expect(screen.getAllByRole('combobox', { name: /Assign track/ })).toHaveLength(1);
    fireEvent.change(screen.getByRole('combobox', { name: 'Assign track 3 to a team' }), { target: { value: 'frc1' } });
    expect(props.onAssign).toHaveBeenCalledWith(3, 'frc1');
    rerender(<TrackIdentityList {...props} identities={{ ...props.identities, 3: 'frc1' }} />);
    expect(screen.getByText(/No paths match/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('checkbox', { name: 'Only unassigned paths' }));
    expect(screen.getByRole('combobox', { name: 'Assign track 3 to a team' })).toHaveValue('frc1');
  });
  it('keeps all match teams selectable when colour is wrong', () => {
    render(<TrackIdentityList {...props} />);
    const select = screen.getByRole('combobox', { name: 'Assign track 2 to a team' });
    expect(Array.from(select.querySelectorAll('option')).map(option => option.value)).toEqual(['', 'frc2', 'frc1']);
  });
  it('offers likely continuations with photos and applies or dismisses them', () => {
    const onApply = vi.fn();
    const onDismiss = vi.fn();
    render(<TrackIdentityList {...props} photoUrls={{ 3: 'blob:three' }} suggestion={{ teamKey: 'frc2', seedId: 2, trackIds: [3] }} onApplySuggestion={onApply} onDismissSuggestion={onDismiss} />);
    const region = screen.getByRole('region', { name: 'Suggested paths for the same robot' });
    expect(region).toHaveTextContent('This path looks like the same robot continuing. Apply 2?');
    expect(screen.getAllByRole('img', { name: 'Robot on track 3' }).length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole('button', { name: 'Apply 2 to it' }));
    expect(onApply).toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Not the same robot' }));
    expect(onDismiss).toHaveBeenCalled();
  });
});
