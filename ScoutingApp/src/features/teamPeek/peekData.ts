// The few numbers the team peek card shows, read from the Team Center intel answer. Anything
// missing is null, and the card shows a dash rather than a made-up zero.

export type TeamPeekData = {
  nickname: string | null;
  rating: number | null;
  fuelRate: string | null;
  climb: string | null;
  record: string | null;
  recordLabel: string;
  status: string | null;
};

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
}

function finite(value: unknown): number | null {
  if (value === null || value === undefined || value === '') return null;
  const parsed = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function wlt(value: unknown): string | null {
  const r = record(value);
  const wins = finite(r?.wins);
  const losses = finite(r?.losses);
  const ties = finite(r?.ties);
  if (wins === null || losses === null) return null;
  return `${wins}-${losses}-${ties ?? 0}`;
}

// TBA's status strings carry <b> tags.
function plain(value: unknown): string | null {
  if (typeof value !== 'string' || !value.trim()) return null;
  return value.replace(/<[^>]+>/g, '').trim();
}

export function peekFromIntel(intel: Record<string, unknown>, teamKey: string): TeamPeekData {
  const team = record(intel.team);
  const rating = record(intel.rating);
  const analysis = record(intel.analysis);
  const averages = record(analysis?.averages);
  const tba = record(intel.tba);
  const status = record(tba?.event_status);
  const qual = record(status?.qual);
  const ranking = record(qual?.ranking);
  const playoff = record(status?.playoff);

  const fuel = finite(averages?.fuel_scoring_rate);
  const climbProb = finite(averages?.climb_success_prob);
  const rank = finite(ranking?.rank);
  const qualRecord = wlt(ranking?.record);
  const playoffRecord = wlt(playoff?.record);

  return {
    nickname: typeof team?.nickname === 'string' && team.nickname.trim() ? team.nickname : `Team ${teamKey.replace(/^frc/, '')}`,
    rating: rating?.available === false ? null : finite(rating?.rating_0_100),
    fuelRate: fuel === null ? null : fuel.toFixed(1),
    climb: climbProb === null ? null : `${Math.round(climbProb * 100)}%`,
    record: qualRecord ?? playoffRecord,
    recordLabel: qualRecord ? (rank !== null ? `Record · rank ${rank}` : 'Record') : playoffRecord ? 'Playoff record' : 'Record',
    status: plain(status?.alliance_status_str),
  };
}
