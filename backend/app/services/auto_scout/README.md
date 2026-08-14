# Auto Scout Service

This service automatically generates scouting form drafts for teams using a combination of video analysis results and ML predictions. The goal is to reduce the manual workload for human scouts — instead of filling out a form from scratch, they review and approve a pre-filled draft.

## What It Does

After a match's video analysis completes, this service reads all the extracted metrics and translates them into structured scouting form fields. It attaches a confidence score to each field so reviewers know which fields are high-confidence predictions and which ones need closer attention.

Human scouts can then approve the draft as-is, make selective overrides, or reject it entirely. Approved drafts also feed back into the ML training pipeline.

## Files

**`scouting.py`**
The main orchestrator. Handles draft generation, approval, and rejection.

- `generate_auto_scout_draft()` — Checks if analysis is complete, builds the feature vector, runs field predictors, and stores the draft with per-field confidence scores. Fields are flagged as either `ready` (high confidence) or `low_confidence` (needs human review).
- `generate_auto_scout_drafts_for_match()` — Batch helper: generates one draft per assigned team for a match. Per-team failures are isolated into an `errors[]` list so one bad team never blocks the other five; returns structured counts (`created_count`, `ready_count`, `low_confidence_count`, `failed_count`, `skipped_existing_count`, etc.). Preserves approved drafts and current ready drafts unless `force_regenerate=True`.
- `approve_auto_scout_draft()` — Records approval, tracks any field-level overrides the scout made, and marks the draft as approved.
- `reject_auto_scout_draft()` — Logs the rejection with a reason for downstream review.
- `summarize_team_auto_scout_profile()` — Rolls up a team's non-superseded drafts into the video-derived "Robot Profile" shown in Team Center (per field: typical value, range, trend, sample size, average confidence).
- `summarize_auto_scout_confidence_calibration()` — Turns approval telemetry into per-field calibration metrics (override rates, worst confidence bucket, a `watch`/`lower_confidence`/`eligible_for_threshold_raise` recommendation) so confidence formulas can be tuned on evidence.

**`backfill.py`**
The reliability net. `backfill_missing_auto_scout_drafts()` finds recent completed analysis runs, skips stale analysis versions, and generates only the missing drafts — bounded by `max_runs` / `max_drafts`. Runs on a scheduler job so matches analyzed before the post-analysis hook shipped (or any run where the hook failed) still get drafts.

**`shift_play.py`**
The offense/defense analysis engine. The 2026 REBUILT match alternates which alliance's hub is active, so the game itself defines when a robot should attack vs. defend. This engine reads that intent out of positional tracking — per robot, per shift it scores offense and defense and produces two heat maps (attack pattern on own shifts, defense pattern on opponent shifts). ORM-free (operates on plain `TrackPoint` lists) so the same logic runs server-side and, later, on-device.

**`on_device.py`**
The device-free core math for the on-device (offline PWA) match breakdown — Part B. Turns per-frame robot detections + a per-frame field homography into the same `TrackPoint` stream `shift_play.py` consumes, with robustness layers (low-confidence filtering, last-good-pose fallback, velocity-spike rejection, median smoothing). Also handles field-corner tap calibration (`calibrate_from_taps`), pose carry through camera motion (`carry_pose`), and closed-set-of-6 bumper-OCR temporal voting for track identity. Deliberately OpenCV-free so the PWA can mirror it in-browser; fully unit-tested.

**`on_device_cv.py`**
The OpenCV counterpart to `on_device.py`, kept separate so the core stays cv2-free. Provides Lucas-Kanade optical-flow camera stabilization (`StabilizedPose`): real-footage testing showed the field's AprilTags are unresolvable from stands distance, so instead of re-detecting tags every frame a scout taps the 4 field corners once and this module carries that base pose frame-to-frame via optical flow.

**`ml.py`**
Feature engineering for the ML predictions. Builds a 35-element feature vector from raw video analysis metrics (tracking data, event counts, zone dwell times, etc.). Also defines which form fields have ML predictors attached.

**`specs.py`**
Configuration for the scouting form itself — field definitions, valid value ranges, and season-specific priors. The 2026 season supports predictions for:

- `auto_mobility`, `auto_scored`, `auto_missed`
- `teleop_scored`, `teleop_under_defense_scored`, `teleop_cycles`
- `offense_level`, `defense_level`, `awareness_level`
- `intake_failures`, `foul_count`, `endgame_mode`

**`training.py`**
Exports approved drafts as ML training snapshots. Each approved draft (with any human overrides noted) becomes a labeled example that can be used to retrain field predictors.

## How It Runs

1. When a match analysis run commits as `completed`, a best-effort hook in `services/jobs.py` calls `generate_auto_scout_drafts_for_match()` for all six assigned teams. Draft-generation failure never flips the analysis run's status; the scheduler-backed `backfill.py` job catches any that were missed.
2. The service verifies video analysis is finished before proceeding.
3. A feature vector is built from the analysis results (tracking, events, findings).
4. Each supported form field runs through its predictor to get a value + confidence score.
5. Additional signals are estimated — disabled periods, defensive engagement, cycle pace.
6. The draft is stored with fields marked `ready` or `low_confidence`.
7. A human scout reviews the draft, approves (with optional overrides), or rejects it.
8. Approved drafts are exported as training data for the next ML cycle.

## Dependencies

- `AnalysisRun` — the completed analysis this draft is based on
- `game_config` — match timing parameters + the `shift_schedule` block used by `shift_play.py`
- `services/jobs.py` — the analysis-completion hook that triggers batch draft generation
- `services/scheduler.py` — runs the backfill catch-up job
- ML model artifacts — stored predictors for supported form fields (see `ml.py`, `shadow.py`)
