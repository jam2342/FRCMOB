// On-device performance harness. The make-or-break device question is whether a phone
// can run the pipeline fast enough (target 2-5 fps) without thermal throttling. This
// times any async stage (detector inference, full per-frame pipeline) on whatever device
// it runs on, so the same code that ships answers the fps question on a real phone.
// Kept dependency-free (no ort) so it unit-tests and can wrap any stage.

export type TimingSummary = {
  iterations: number;
  msMedian: number;
  msP90: number;
  msMax: number;
  fps: number; // derived from the median
};

export function summarizeTimings(samples: number[]): TimingSummary {
  if (samples.length === 0) return { iterations: 0, msMedian: 0, msP90: 0, msMax: 0, fps: 0 };
  const s = [...samples].sort((a, b) => a - b);
  const pct = (p: number) => s[Math.min(s.length - 1, Math.floor(p * s.length))];
  const mid = Math.floor(s.length / 2);
  const msMedian = s.length % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2;
  return {
    iterations: s.length,
    msMedian,
    msP90: pct(0.9),
    msMax: s[s.length - 1],
    fps: msMedian > 0 ? 1000 / msMedian : 0,
  };
}

// Run an async stage `warmup` then `iterations` times, timing each, and summarize.
// Detect thermal drift by comparing the first vs last third of samples (a phone that
// throttles shows the tail slowing down).
export async function benchmark(
  run: () => Promise<unknown>,
  opts: { iterations?: number; warmup?: number } = {},
): Promise<TimingSummary & { thermalDriftPct: number }> {
  const iterations = opts.iterations ?? 30;
  const warmup = opts.warmup ?? 3;
  for (let i = 0; i < warmup; i++) await run();
  const samples: number[] = [];
  for (let i = 0; i < iterations; i++) {
    const t = performance.now();
    await run();
    samples.push(performance.now() - t);
  }
  const summary = summarizeTimings(samples);
  const third = Math.max(1, Math.floor(samples.length / 3));
  const mean = (xs: number[]) => xs.reduce((a, b) => a + b, 0) / Math.max(1, xs.length);
  const first = mean(samples.slice(0, third));
  const last = mean(samples.slice(-third));
  const thermalDriftPct = first > 0 ? Math.round(((last - first) / first) * 100) : 0;
  return { ...summary, thermalDriftPct };
}
