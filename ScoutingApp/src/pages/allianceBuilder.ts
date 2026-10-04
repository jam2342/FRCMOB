// How the alliance score is split. These were fixed at 50/30/20 here while
// Compare had its own sliders on the same endpoint; now they live here only.
export const DEFAULT_WEIGHTS = { compatibility: 50, pros: 30, cons: 20 };

export function normalizedWeights(weights: { compatibility: number; pros: number; cons: number }) {
  const c = Math.max(0, weights.compatibility);
  const p = Math.max(0, weights.pros);
  const k = Math.max(0, weights.cons);
  const sum = c + p + k;
  if (sum <= 0) return { compatibility: 0.5, pros: 0.3, cons: 0.2 };
  return { compatibility: c / sum, pros: p / sum, cons: k / sum };
}

// Compare hands its teams over as ?teams=frc1,frc2,frc3.
export function teamsFromParam(raw: string | null): [string, string, string] {
  const keys = String(raw || '')
    .split(',')
    .map((value) => value.trim().toLowerCase())
    .filter((value, index, all) => /^frc\d+$/.test(value) && all.indexOf(value) === index)
    .slice(0, 3);
  return [keys[0] || '', keys[1] || '', keys[2] || ''];
}
