// A value the app does not have reads the same everywhere. It was "N/A" wherever metric() ran and
// "—" wherever a page wrote its own fallback, often both on one screen (Compare: "TBA Rank N/A"
// beside a "—" trend).
export const MISSING = '—';

// A rating or points move as "+1.2" / "−0.3" / "0.0": never "-0.0" for a move that rounds away.
export function signedChange(value: number, digits = 1): string {
  const rounded = Number(value.toFixed(digits));
  if (rounded === 0 || Number.isNaN(rounded)) return (0).toFixed(digits);
  return `${rounded > 0 ? '+' : '−'}${Math.abs(rounded).toFixed(digits)}`;
}
