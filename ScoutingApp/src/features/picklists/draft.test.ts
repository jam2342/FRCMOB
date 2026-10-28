import { describe, expect, it } from 'vitest';
import type { PicklistSlot } from '../../api';
import { bestAvailable, canBePicked, declineTeam, draftFromSlots, pickTeam, rankedTeamKeys, seedCaptains, standingsFromForecast } from './draft';

const team = (n: number) => `frc${n}`;
// Ranked 1..12 at the event; our list prefers 11, 12, 9, 10 and avoids 7.
const RANKED = Array.from({ length: 12 }, (_, i) => team(i + 1));
function slot(n: number, tier: PicklistSlot['tier'] = 'first'): PicklistSlot {
  return { team_key: team(n), tier, status: 'available', picked_by_alliance: null, dnp_reason: '', notes: '' };
}
const LIST = [slot(11), slot(12), slot(9), slot(10), slot(2), slot(7, 'dnp')];

describe('alliance selection draft', () => {
  it('seeds the top eight as captains, adding ranked teams the list left out', () => {
    const seeded = seedCaptains(LIST, RANKED);
    const state = draftFromSlots(seeded);
    expect(state.alliances.map((a) => a.captain)).toEqual(RANKED.slice(0, 8));
    expect(state.onClock).toEqual({ alliance: 1, round: 1 });
    // Our order is kept; the added captains go to the bottom.
    expect(seeded.slice(0, 6).map((s) => s.team_key)).toEqual(LIST.map((s) => s.team_key));
    expect(seeded).toHaveLength(LIST.length + 6);
  });

  it('picks in serpentine order: 1→8, then 8→1', () => {
    let slots = seedCaptains(LIST, RANKED);
    const order: number[] = [];
    for (const n of [9, 10, 11, 12]) {
      order.push(draftFromSlots(slots).onClock!.alliance);
      slots = pickTeam(slots, team(n), RANKED);
    }
    expect(order).toEqual([1, 2, 3, 4]);
    const state = draftFromSlots(slots);
    expect(state.alliances[2].picks).toEqual([team(11)]);
    expect(state.picksMade).toBe(4);
  });

  it('turns to round two at alliance 8 and finishes after sixteen picks', () => {
    let slots = seedCaptains([], RANKED.concat(Array.from({ length: 20 }, (_, i) => team(100 + i))));
    const ranked = rankedTeamKeys({ rankings: slots.map((s, i) => ({ team_key: s.team_key, rank: i + 1 })) });
    const clocks: number[] = [];
    for (let i = 0; i < 16; i += 1) {
      clocks.push(draftFromSlots(slots).onClock!.alliance);
      slots = pickTeam(slots, team(100 + i), ranked);
    }
    expect(clocks).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 8, 7, 6, 5, 4, 3, 2, 1]);
    expect(draftFromSlots(slots).onClock).toBeNull();
    expect(draftFromSlots(slots).alliances.every((a) => a.picks.length === 2)).toBe(true);
  });

  it('moves the alliances below up when a captain accepts, and promotes the next ranked team', () => {
    let slots = seedCaptains(LIST, RANKED);
    slots = pickTeam(slots, team(2), RANKED); // A1 picks the #2 captain
    const state = draftFromSlots(slots);
    expect(state.alliances[0]).toMatchObject({ captain: team(1), picks: [team(2)] });
    expect(state.alliances.map((a) => a.captain)).toEqual([1, 3, 4, 5, 6, 7, 8, 9].map(team));
    expect(state.onClock).toEqual({ alliance: 2, round: 1 });
  });

  it('goes back to the alliance whose pick was undone, not just the next in line', () => {
    let slots = seedCaptains(LIST, RANKED);
    slots = pickTeam(slots, team(11), RANKED); // A1
    slots = pickTeam(slots, team(12), RANKED); // A2
    // A1's pick undone from the list.
    slots = slots.map((s) => (s.team_key === team(11) ? { ...s, status: 'available' as const, picked_by_alliance: null } : s));
    expect(draftFromSlots(slots).onClock).toEqual({ alliance: 1, round: 1 });
    slots = pickTeam(slots, team(9), RANKED);
    expect(draftFromSlots(slots).onClock).toEqual({ alliance: 3, round: 1 });
  });

  it("won't pick a captain above the picker, a team on an alliance, or anyone once selection is over", () => {
    let slots = seedCaptains(LIST, RANKED);
    slots = pickTeam(slots, team(11), RANKED); // A1
    const state = draftFromSlots(slots);
    expect(canBePicked(state, team(1))).toBe(false);
    expect(canBePicked(state, team(11))).toBe(false);
    expect(canBePicked(state, team(5))).toBe(true);
    expect(pickTeam(slots, team(1), RANKED)).toEqual(slots);
    expect(canBePicked(draftFromSlots(LIST), team(11))).toBe(false);
  });

  it('lists the best available in our order, skipping DNP, declined and taken teams', () => {
    let slots = seedCaptains(LIST, RANKED);
    slots = declineTeam(slots, team(11));
    slots = pickTeam(slots, team(12), RANKED);
    const best = bestAvailable(slots, draftFromSlots(slots), 3).map((s) => s.team_key);
    // A2 (captain 2) picks next: 2 can't pick itself, 1 captains above it, but A3's captain 3
    // is still up for grabs in round one.
    expect(best).toEqual([team(9), team(10), team(3)]);
  });

  it('leaves a captain who declines on their alliance', () => {
    const slots = seedCaptains(LIST, RANKED);
    expect(declineTeam(slots, team(3))).toEqual(slots);
    const state = draftFromSlots(slots, new Set([team(3)]));
    expect(state.alliances[2].captain).toBe(team(3));
    expect(canBePicked(state, team(3))).toBe(false);
    expect(canBePicked(state, team(4))).toBe(true);
  });

  it('never lets a team that declined be picked later', () => {
    let slots = seedCaptains(LIST, RANKED);
    slots = declineTeam(slots, team(11));
    expect(canBePicked(draftFromSlots(slots), team(11))).toBe(false);
    expect(pickTeam(slots, team(11), RANKED)).toEqual(slots);
  });

  it('reads TBA rankings best first and ignores junk rows', () => {
    expect(rankedTeamKeys({ rankings: [{ team_key: 'frc3', rank: 2 }, { team_key: 'FRC9', rank: 1 }, { team_key: 'x', rank: 3 }, { rank: 4 }] })).toEqual(['frc9', 'frc3']);
    expect(rankedTeamKeys(null)).toEqual([]);
  });
});

describe('standings from the results', () => {
  it('orders teams by their current rank, leaving out teams with none', () => {
    const forecast = { ok: true, event_key: 'e', teams: [
      { team_key: 'frc5', current_rank: 2 }, { team_key: 'FRC9', current_rank: 1 }, { team_key: 'frc7', current_rank: null },
    ] } as never;
    expect(standingsFromForecast(forecast)).toEqual(['frc9', 'frc5']);
  });
});
