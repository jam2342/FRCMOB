# Paired held-out production-network comparison

An offline 2026 study. It pairs a completed weekly rehearsal's saved forecasts with
production's team model and correction network, retrained on the same clock, and
scores both on identical matches. It does not select, activate or serve anything.

Both pipelines see the same information:

- The match set is the source rehearsal's official-event records. Production's
  replay must agree on every key, actual time, lineup and score.
- A forecast at a match's actual start sees only results whose TBA
  `post_result_time` is strictly earlier. Both team models observe posted results
  in playing order; a late result for an earlier match rewinds and replays.
- Weekly jobs run Monday 09:00 UTC on labels posted before the job. Versions
  publish at 10:00 and apply to strictly later forecasts.

Two comparisons are kept apart:

- **Rolling online** (like-for-like). The candidate is the source rehearsal's saved
  active forecasts. The network is refitted every Monday with
  `scripts/train_win_correction.fit` on every 2026 label posted before the job,
  using features from a fresh playing-order replay of those results. With fewer than
  150 labels it serves production's base probability, as production does for a
  season without weights. Weights are rounded as the trainer writes them and served
  through production's numpy forward pass.
- **Static held-out.** Both pipelines keep the parameters in effect for the first
  newest-event forecast; their states keep observing posted results. The frozen
  candidate is replayed from the source's read-only SQLite and must reproduce the
  saved forecasts exactly while that version was active.

Production's own 2026 weights were trained on every 2026 event. They appear only as
an in-sample reference, never in the gate.

```bash
cd backend
.venv/bin/python scripts/evaluate_rating_network_pairing.py --step freeze \
  --out /private/network-pairing --source /private/fixed-prior/runs/2026 \
  --cache /private/tba --priors /private/prod/team_prior_strengths_2025.csv

.venv/bin/python scripts/evaluate_rating_network_pairing.py --step parity \
  --protocol /private/network-pairing/protocols/<sha>.json --all-cache /private/tba-all ...

.venv/bin/python scripts/evaluate_rating_network_pairing.py --step run \
  --protocol /private/network-pairing/protocols/<sha>.json ...
```

Freeze records the policy, source identity, input and code hashes, and numpy/torch
versions; later steps refuse any change. Parity requires the replay to reproduce
phase 3's production replay exactly. The run saves each weekly network, the
per-match file (keys, actual and publication times, eligibility, features, every
probability and its parameter hash) and the report. Source files are rechecked.

Scoring follows the source rehearsal's win rule: ties and matches without
explainable points on both sides are excluded and counted. Paired differences
use 90% event-cluster intervals (2,000 draws, seed 7) over the source's slices.
The gate applies `research_rehearsal.noninferiority` with margin 0 to rolling
newest-event Brier, candidate minus network. Late-event non-regression counts as
established only if that passes and the static check has not failed.

Limitations: 2026 is examined development data. Production's team-model constants
and bucket scales were tuned on 2026 and cannot be held out. Production as served
also saw preseason and offseason events. The network has no earlier-season start.
Cached labels lack correction-publication times. Passing this check is necessary for
phase 7, not sufficient for serving.
