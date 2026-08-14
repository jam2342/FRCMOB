// Closed-set-of-6 bumper-OCR temporal voting — in-browser mirror of
// on_device.vote_track_identity. Never free-OCR: score each per-frame read against the
// match's 6 known team numbers, weight by OCR confidence, aggregate over the whole
// track (one track = one robot), and defer to tap-ID when no candidate clearly wins.

export type OcrRead = { text: string; confidence?: number };

export type IdentityVote = {
  teamKey: string | null; // winner, or null when unresolved (tap-ID fallback)
  confidence: number;
  resolved: boolean;
  scores: Record<string, number>; // per-candidate vote share, 0..1
};

const MIN_READ_SIMILARITY = 0.5;
const MIN_VOTE_SHARE = 0.45;
const MIN_WINNER_MARGIN = 0.15;

function digits(text: string): string {
  return (text.match(/\d/g) ?? []).join('');
}

// Longest common substring (start indices + length) — the core of Ratcliff/Obershelp.
function longestMatch(a: string, b: string): { i: number; j: number; k: number } {
  let best = { i: 0, j: 0, k: 0 };
  const prev = new Array(b.length + 1).fill(0);
  for (let i = 0; i < a.length; i++) {
    const curr = new Array(b.length + 1).fill(0);
    for (let j = 0; j < b.length; j++) {
      if (a[i] === b[j]) {
        curr[j + 1] = prev[j] + 1;
        if (curr[j + 1] > best.k) best = { i: i - curr[j + 1] + 1, j: j - curr[j + 1] + 1, k: curr[j + 1] };
      }
    }
    prev.splice(0, prev.length, ...curr);
  }
  return best;
}

// difflib-style similarity ratio (Ratcliff/Obershelp): 2*M/T where M is total matched
// characters across recursively-found matching blocks. Same metric the backend uses.
export function similarityRatio(a: string, b: string): number {
  if (a.length === 0 && b.length === 0) return 1;
  const total = a.length + b.length;
  if (total === 0) return 0;
  const matched = (sa: string, sb: string): number => {
    const m = longestMatch(sa, sb);
    if (m.k === 0) return 0;
    return m.k + matched(sa.slice(0, m.i), sb.slice(0, m.j)) + matched(sa.slice(m.i + m.k), sb.slice(m.j + m.k));
  };
  return (2 * matched(a, b)) / total;
}

export function voteTrackIdentity(reads: OcrRead[], candidateTeamKeys: string[]): IdentityVote {
  const candidates = candidateTeamKeys.map((c) => String(c).trim().toLowerCase()).filter(Boolean);
  if (candidates.length === 0) return { teamKey: null, confidence: 0, resolved: false, scores: {} };

  const scores: Record<string, number> = Object.fromEntries(candidates.map((c) => [c, 0]));
  const candDigits = Object.fromEntries(candidates.map((c) => [c, digits(c) || c]));

  for (const read of reads) {
    const rd = digits(read.text);
    if (!rd) continue;
    const weight = Math.max(0, read.confidence ?? 1);
    if (weight <= 0) continue;
    for (const cand of candidates) {
      const sim = similarityRatio(rd, candDigits[cand]);
      if (sim >= MIN_READ_SIMILARITY) scores[cand] += sim * weight;
    }
  }

  const total = Object.values(scores).reduce((s, v) => s + v, 0);
  if (total <= 0) return { teamKey: null, confidence: 0, resolved: false, scores };

  const shares = Object.fromEntries(Object.entries(scores).map(([c, v]) => [c, v / total]));
  const ranked = Object.entries(shares).sort((a, b) => b[1] - a[1]);
  const [winner, winnerShare] = ranked[0];
  const runnerShare = ranked[1]?.[1] ?? 0;
  const resolved = winnerShare >= MIN_VOTE_SHARE && winnerShare - runnerShare >= MIN_WINNER_MARGIN;
  const round4 = (v: number) => Math.round(v * 1e4) / 1e4;
  return {
    teamKey: resolved ? winner : null,
    confidence: round4(winnerShare),
    resolved,
    scores: Object.fromEntries(Object.entries(shares).map(([c, v]) => [c, round4(v)])),
  };
}
