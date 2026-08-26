/* 1 -> "1st". Used wherever a rank is shown, because "rank 4" reads as a
   quantity and "4th" reads as a position.
 *
 * The 11/12/13 exception is the whole reason this is a function: 11 is
 * eleventh, not eleven-first, and it recurs at 111, 211 and so on. */
export function ordinal(value: number): string {
  const n = Math.trunc(value);
  const abs = Math.abs(n);
  const lastTwo = abs % 100;
  if (lastTwo >= 11 && lastTwo <= 13) return `${n}th`;
  switch (abs % 10) {
    case 1: return `${n}st`;
    case 2: return `${n}nd`;
    case 3: return `${n}rd`;
    default: return `${n}th`;
  }
}
