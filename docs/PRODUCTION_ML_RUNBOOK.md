# ML production runbook

Detector weights are intentionally excluded from Git. A deploy is ready only when the backend and browser model artifacts are explicitly provisioned.

## Required release inputs

The server runs no detector since the broadcast pipeline was retired (2026-09-28). The
detector ships to phones as ONNX.

1. Before release, run the locked detector evaluation against the exact `.pt` the ONNX
   is exported from (on the training machine, with `backend/requirements-training.txt`):

   ```sh
   cd backend
   PYTHONPATH=. python scripts/verify_holdout.py --model /path/to/frc_robot_detector_v2.pt
   ```

2. Export it (`scripts/export_on_device_model.py`) and publish the ONNX at a public,
   HTTPS, CORS-enabled URL, or commit it under `ScoutingApp/public/models/` under a new
   filename and update `DEFAULT_MODEL_URL` in `modelArtifact.ts` (`/models/` is cached
   for a week). Set `VITE_ONDEVICE_MODEL_URL` for a hosted copy. A production frontend
   build fails if neither the URL nor a local ONNX artifact is available.
3. Deploy backend migrations before or with the backend image, then verify
   `GET /health/deep` returns `ok: true`.

## Shadow-model release policy

Automatic training and activation are off by default. Train a candidate with `activate=false`, evaluate its time-split metrics and calibration, materialize predictions for a representative event, and only then activate it through an intentional admin release. Do not promote a model solely because it trained successfully.

Implicit inference now requires an active model, including when an environment
setting pins a version. An explicit model-version argument can evaluate an
inactive candidate. The training CLI also defaults to no activation; `--activate`
is an intentional release action. For a prior-event comparison, use:

```sh
python scripts/evaluate_models_pre_event.py --season 2026 \
  --match-outcome-model-version <candidate-match-version> \
  --team-strength-model-version <candidate-team-version>
```

See [the October 2 audit](BETA_RELIABILITY_2026-10-02.md) for the production
inactive-model fallback finding, current candidate results, and proposed rollout.

### Pre-match retrain (2026-10-04)

The shadow models trained on end-of-event ratings, which contain the matches they
predict. `scripts/evaluate_pre_match_training.py` (read-only) rebuilds the rows from
pre-match information only: each team's prior-event rating, confidence and official
fuel rate, plus its official fuel rate in earlier matches at the same event (shrunk
toward the prior rate, weight n/(n+3)). It trains on older 2026 events and scores the
same newest-20% holdout (56 events, 2,140 matches where all six teams had a prior event):

| Match outcome (holdout) | Accuracy | Brier | ECE |
|---|---|---|---|
| Shipped formula, prior-event rating | 61.3% | 0.237 | 0.072 |
| Logistic, prior-event features only | 67.4% | 0.223 | 0.122 |
| Logistic, blended fuel margin (one feature) | 69.2% | 0.203 | 0.032 |
| Logistic, all pre-match features | 72.6% | 0.187 | 0.083 |
| Shadow network, all pre-match features | 72.4% | 0.189 | 0.068 |
| Formula with end-of-event ratings (leaky upper bound) | 82.2% | 0.139 | 0.112 |

Team strength gains nothing: last event's fuel rate MAE 13.7 / Spearman 0.631; linear
13.2 / 0.631; network 13.5 / 0.614 (1,006 holdout rows). Keep it inactive.

What this means: with honest inputs, a model clearly beats the formula on the same
pre-match information, and most of the gain is official fuel data, not the network (the
one-feature logistic is the best calibrated). Nothing was activated, because:
the model needs a prior event for all six teams (9,838 of the season's matches had
none, including every week-1 match); the serving path would have to compute the same
as-of-match features; and the formula's real mid-event accuracy (its ratings update
during the event) sits somewhere between 61% and the leaky 82%, which can't be measured
without replaying ratings match by match. The simplest next step is a fuel-margin
formula with a rating fallback, not activating the network.

Model artifacts are written atomically, so a process can never load a partially written candidate. Keep the backend media volume persistent; it holds the active shadow artifacts.

## Release checks

- `docker compose -f docker/docker-compose.prod.yml config` with the required non-secret variables available.
- Backend tests in a Python 3.11 environment with `backend/requirements.txt` installed.
- `cd ScoutingApp && npm run lint && npm run test -- --run && npm run build`.
- Verify one real match through on-device recording, sync, review, and auto-scout draft review before enabling the new model for scouts.
