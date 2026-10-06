// Ranks skip do-not-pick rows, so "move to rank N" means "to where the team
// ranked N is now". Past the last rank, it goes to the end of the list.
export function slotIndexForRank(slots: ReadonlyArray<{ tier: string }>, rank: number): number {
  let seen = 0;
  for (let i = 0; i < slots.length; i += 1) {
    if (slots[i].tier === 'dnp') continue;
    seen += 1;
    if (seen === rank) return i;
  }
  return Math.max(0, slots.length - 1);
}
