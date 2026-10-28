import type { EventForecastResponse, PicklistSlot, PicklistSlotTier } from '../../api';

// Alliance selection on a picklist: eight alliances, a captain each, then picks in serpentine
// order (round one 1→8, round two 8→1). Everything lives in the picklist's own slots —
// `status: 'captain' | 'picked'` with `picked_by_alliance` — so the board is shared with the team
// and saved like any other edit, with no new storage.
export const ALLIANCE_COUNT = 8;
export const PICK_ORDER: readonly number[] = [
  ...Array.from({ length: ALLIANCE_COUNT }, (_, i) => i + 1),
  ...Array.from({ length: ALLIANCE_COUNT }, (_, i) => ALLIANCE_COUNT - i),
];

export type Alliance = { number: number; captain: string | null; picks: string[] };

export type DraftState = {
  alliances: Alliance[];
  picksMade: number;
  // Who picks next; null before captains are set, and once every pick is made.
  onClock: { alliance: number; round: 1 | 2 } | null;
  // Teams already on an alliance (captains and picks).
  taken: ReadonlySet<string>;
  // Teams who turned down an invitation, captains included: nobody can pick them after.
  refused: ReadonlySet<string>;
  hasCaptains: boolean;
};

// `refusedCaptains`: captains who declined. A captain's slot already says which alliance they
// lead, so the board keeps those itself; everyone else who declined is marked in their slot.
export function draftFromSlots(slots: readonly PicklistSlot[], refusedCaptains: ReadonlySet<string> = new Set()): DraftState {
  const refused = new Set(refusedCaptains);
  for (const slot of slots) if (slot.status === 'declined') refused.add(slot.team_key);
  const alliances: Alliance[] = Array.from({ length: ALLIANCE_COUNT }, (_, i) => ({ number: i + 1, captain: null, picks: [] }));
  const taken = new Set<string>();
  let picksMade = 0;
  for (const slot of slots) {
    const number = slot.picked_by_alliance;
    if (!number || number < 1 || number > ALLIANCE_COUNT) continue;
    const alliance = alliances[number - 1];
    if (slot.status === 'captain' && !alliance.captain) {
      alliance.captain = slot.team_key;
      taken.add(slot.team_key);
    } else if (slot.status === 'picked') {
      alliance.picks.push(slot.team_key);
      taken.add(slot.team_key);
      picksMade += 1;
    }
  }
  const hasCaptains = alliances.every((alliance) => alliance.captain);
  // Whose turn: the first seat in pick order its alliance hasn't filled. Counting picks alone
  // went wrong once an earlier pick was undone from the list.
  let onClock: DraftState['onClock'] = null;
  if (hasCaptains) {
    const owed = new Map<number, number>();
    for (let i = 0; i < PICK_ORDER.length; i += 1) {
      const number = PICK_ORDER[i];
      owed.set(number, (owed.get(number) ?? 0) + 1);
      if (alliances[number - 1].picks.length < (owed.get(number) ?? 0)) {
        onClock = { alliance: number, round: i < ALLIANCE_COUNT ? 1 : 2 };
        break;
      }
    }
  }
  return { alliances, picksMade, onClock, taken, refused, hasCaptains };
}

// TBA/FIRST rankings → team keys, best first.
export function rankedTeamKeys(rankings: Record<string, unknown> | null | undefined): string[] {
  const rows = Array.isArray(rankings?.rankings) ? (rankings.rankings as Array<Record<string, unknown>>) : [];
  return rows
    .map((row) => ({ team: String(row.team_key ?? '').toLowerCase(), rank: Number(row.rank) }))
    .filter((row) => /^frc\d+$/.test(row.team) && Number.isFinite(row.rank))
    .sort((a, b) => a.rank - b.rank)
    .map((row) => row.team);
}

// The standings worked out from the official results (the forecast's current ranks), for when
// TBA has no rankings for the event yet.
export function standingsFromForecast(forecast: EventForecastResponse): string[] {
  return (forecast.teams ?? [])
    .filter((row) => typeof row.current_rank === 'number')
    .sort((a, b) => (a.current_rank ?? 0) - (b.current_rank ?? 0))
    .map((row) => row.team_key.toLowerCase());
}

function blankSlot(teamKey: string, tier: PicklistSlotTier = 'second'): PicklistSlot {
  return { team_key: teamKey, tier, status: 'available', picked_by_alliance: null, dnp_reason: '', notes: '' };
}

function withTeams(slots: readonly PicklistSlot[], teamKeys: readonly string[]): PicklistSlot[] {
  const next = slots.slice();
  const present = new Set(next.map((slot) => slot.team_key));
  for (const key of teamKeys) {
    if (!present.has(key)) {
      next.push(blankSlot(key));
      present.add(key);
    }
  }
  return next;
}

function setSlot(slots: PicklistSlot[], teamKey: string, patch: Partial<PicklistSlot>): void {
  const index = slots.findIndex((slot) => slot.team_key === teamKey);
  if (index >= 0) slots[index] = { ...slots[index], ...patch };
}

// The top eight become captains of alliances 1-8. Any captain or pick already on the board is
// cleared first: seeding starts selection over. Ranked teams missing from the list are added at
// the bottom, so a team the list left out can still be a captain.
export function seedCaptains(slots: readonly PicklistSlot[], ranked: readonly string[]): PicklistSlot[] {
  const captains = ranked.slice(0, ALLIANCE_COUNT);
  const next = withTeams(slots, captains).map((slot) => (
    slot.status === 'captain' || slot.status === 'picked'
      ? { ...slot, status: 'available' as const, picked_by_alliance: null }
      : slot
  ));
  captains.forEach((team, i) => setSlot(next, team, { status: 'captain', picked_by_alliance: i + 1 }));
  return next;
}

// The on-the-clock alliance picks `teamKey`. A captain further down who accepts leaves their
// alliance: the alliances below move up one and the best-ranked team not yet on an alliance
// becomes the new eighth captain (a team that declined earlier can still captain).
export function pickTeam(slots: readonly PicklistSlot[], teamKey: string, ranked: readonly string[]): PicklistSlot[] {
  const state = draftFromSlots(slots);
  if (!state.onClock || !canBePicked(state, teamKey)) return slots.slice();
  const picker = state.onClock.alliance;
  const next = withTeams(slots, [teamKey]);
  const leaving = state.alliances.find((alliance) => alliance.captain === teamKey);
  setSlot(next, teamKey, { status: 'picked', picked_by_alliance: picker });
  if (leaving) {
    for (const alliance of state.alliances) {
      if (alliance.number <= leaving.number || !alliance.captain) continue;
      setSlot(next, alliance.captain, { picked_by_alliance: alliance.number - 1 });
    }
    const promoted = ranked.find((team) => !state.taken.has(team));
    if (promoted) {
      const withPromoted = withTeams(next, [promoted]);
      setSlot(withPromoted, promoted, { status: 'captain', picked_by_alliance: ALLIANCE_COUNT });
      return withPromoted;
    }
  }
  return next;
}

function isCaptainBelow(state: DraftState, teamKey: string, picker: number): boolean {
  const alliance = state.alliances.find((row) => row.captain === teamKey);
  // Only round one: by round two every captain has an alliance of their own to finish.
  return Boolean(alliance && alliance.number > picker && state.onClock?.round === 1);
}

export function canBePicked(state: DraftState, teamKey: string): boolean {
  if (!state.onClock || state.refused.has(teamKey)) return false;
  return !state.taken.has(teamKey) || isCaptainBelow(state, teamKey, state.onClock.alliance);
}

// A declined team can't be picked later. A captain who declines stays captain of their own
// alliance, so their slot is left alone (the board keeps them out of the picks instead).
export function declineTeam(slots: readonly PicklistSlot[], teamKey: string): PicklistSlot[] {
  if (slots.some((slot) => slot.team_key === teamKey && slot.status === 'captain')) return slots.slice();
  const next = withTeams(slots, [teamKey]);
  setSlot(next, teamKey, { status: 'declined', picked_by_alliance: null });
  return next;
}

// Our list's order, minus anyone on an alliance, who declined, or is marked do-not-pick.
export function bestAvailable(slots: readonly PicklistSlot[], state: DraftState, limit: number): PicklistSlot[] {
  return slots
    .filter((slot) => slot.tier !== 'dnp' && slot.status !== 'declined' && canBePicked(state, slot.team_key))
    .slice(0, limit);
}

export function allianceOf(state: DraftState, teamKey: string | null): Alliance | null {
  if (!teamKey) return null;
  return state.alliances.find((alliance) => alliance.captain === teamKey || alliance.picks.includes(teamKey)) ?? null;
}
