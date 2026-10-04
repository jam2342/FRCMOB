import { SEASON } from '../../config/season';

// How fast a clip already on disk may be walked. At 1x the sampler sits idle ~76% of the
// wall clock waiting for frames the file already contains, so a full match costs a full
// match's duration. Playback is sped up to whatever this device's *measured* per-frame
// cost can keep pace with -- earned from real timings rather than assumed, so a slow
// phone measures a high cost and simply stays at real time.

export const SPEEDUP_WARMUP_SAMPLES = 6;

// The cap is measured, and it is a *presentation* limit rather than a compute one. On a
// real Metal GPU 2x sampled every target frame, while 3x missed some even with the
// detector switched off entirely: past 2x the browser stops presenting a distinct frame
// for every interval the sampler is trying to hit. Those misses are not spread evenly --
// each one doubles the gap between kept frames -- and a full-match run at 3x dropped
// tracks from 14 to 10 because the tracker could no longer associate across the gaps.
// Raise this only against a re-measured frame count AND track count.
export const SPEEDUP_MAX_RATE = 2;

// Spend at most half the inter-sample budget on the sample itself, so a frame slower than
// the median still lands before the next one is due.
const SPEEDUP_BUDGET_SHARE = 0.5;

// Median, not mean: the first sample carries one-off shader compilation and would drag a
// mean upward into a needlessly timid rate.
export function safePlaybackRate(intervalSec: number, costsMs: number[]): number {
  if (costsMs.length === 0) return 1;
  const sorted = [...costsMs].sort((a, b) => a - b);
  const medianMs = sorted[Math.floor(sorted.length / 2)];
  if (!(medianMs > 0)) return 1;
  const budgetMs = intervalSec * 1000 * SPEEDUP_BUDGET_SHARE;
  return Math.max(1, Math.min(SPEEDUP_MAX_RATE, Math.floor(budgetMs / medianMs)));
}

// Advance the sampling target on a fixed grid.
//
// The naive form -- "take a frame whenever intervalSec has passed since the last one I
// took" -- drifts. A presented frame essentially never lands exactly on a boundary, so
// each sample re-anchors the schedule a little late and the overshoot compounds. Over a
// 165 s match at 3 fps that silently cost 11 of 495 samples, and because the loss shows
// up as widened gaps rather than an even thinning, it cost whole tracks: the tracker
// could not associate across them.
//
// Anchoring to the grid instead keeps lateness from accumulating. If a slow frame put us
// more than a whole interval behind, resync to now rather than firing a catch-up burst of
// back-to-back samples, which would cluster frames instead of spacing them.
export function advanceSampleTarget(
  currentTarget: number,
  frameTimeSec: number,
  intervalSec: number,
): number {
  const next = (currentTarget === -Infinity ? frameTimeSec : currentTarget) + intervalSec;
  return next <= frameTimeSec ? frameTimeSec + intervalSec : next;
}

// Seconds kept after the match's scheduled end, for a slightly late buzzer or clock.
const MATCH_END_GRACE_SEC = 2;

// Only the match itself is worth a detector pass: frames before the anchored start were
// already dropped after processing, and scouts often film well before and after.
export function matchWindow(anchorSec: number, durationSec: number): { startSec: number; endSec: number } {
  const startSec = Math.max(0, anchorSec);
  return { startSec, endSec: Math.min(durationSec, startSec + SEASON.matchSec + MATCH_END_GRACE_SEC) };
}

// Did sampling stop short of the window's end? A stalled or dropped video source ends the
// frame loop the same way the last frame does, so the only tell is where it stopped.
export function stoppedEarly(lastSampleSec: number, endSec: number, intervalSec: number): boolean {
  return lastSampleSec < endSec - Math.max(2, 3 * intervalSec);
}
