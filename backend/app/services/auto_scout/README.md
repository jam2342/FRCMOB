# Auto Scout Service

This service generates scouting form drafts from operator-accepted phone recordings. The goal is to reduce the manual workload for human scouts — instead of filling out a form from scratch, they review and approve a pre-filled draft.

## What It Does

Once an operator accepts a phone recording of a match, this service reads the robot's positions and fills what positions can honestly tell: offense and defense levels (from `shift_play.py`), a possible disabled period and zone dwell. Everything else (scoring, misses, fouls, intake, endgame) stays blank for the scout, because a phone recording carries positions only.

Human scouts approve the draft as-is, change fields, or reject it.

The per-field scoring predictors, their ML models and the approved-draft training export were removed on 2026-10-05: phone recordings never carry the findings or match events they needed, so they never ran.

## Files

**`scouting.py`**
The main orchestrator. Handles draft generation, approval, and rejection.

- `generate_auto_scout_draft()` — Picks the best accepted recording of the match, fills offense/defense from shift play plus the track-based insights, and stores the draft with per-field confidence. A draft is `ready` or `low_confidence` (an insight below 0.72 confidence).
- `generate_auto_scout_drafts_for_match()` — Batch helper: generates one draft per assigned team for a match. Per-team failures are isolated into an `errors[]` list so one bad team never blocks the other five; returns structured counts (`created_count`, `ready_count`, `low_confidence_count`, `failed_count`, `skipped_existing_count`, etc.). Analyses each recording once for all six robots. Preserves approved drafts and current ready drafts unless `force_regenerate=True`.
- `approve_auto_scout_draft()` — Records approval, tracks any field-level overrides the scout made, and marks the draft as approved.
- `reject_auto_scout_draft()` — Logs the rejection with a reason for downstream review.
- `summarize_team_auto_scout_profile()` — Rolls up a team's non-superseded drafts into the "Robot Profile" shown in Team Center (per field: typical value, range, trend, sample size, average confidence).

**`backfill.py`**
The reliability net. `backfill_missing_auto_scout_drafts()` finds recently accepted phone recordings, skips stale analysis versions, and generates only the missing drafts — bounded by `max_runs` / `max_drafts`. Runs on a scheduler job so matches analyzed before the post-analysis hook shipped (or any run where the hook failed) still get drafts.

**`shift_play.py`**
The offense/defense analysis engine. The 2026 REBUILT match alternates which alliance's hub is active, so the game itself defines when a robot should attack vs. defend. This engine reads that intent out of positional tracking — per robot, per shift it scores offense and defense and produces two heat maps (attack pattern on own shifts, defense pattern on opponent shifts). ORM-free (operates on plain `TrackPoint` lists) so the same logic runs server-side and, later, on-device.

**`on_device.py`**
The device-free core math for the on-device (offline PWA) match breakdown — Part B. Turns per-frame robot detections + a per-frame field homography into the same `TrackPoint` stream `shift_play.py` consumes, with robustness layers (low-confidence filtering, last-good-pose fallback, velocity-spike rejection, median smoothing). Also handles field-corner tap calibration (`calibrate_from_taps`), pose carry through camera motion (`carry_pose`), and closed-set-of-6 bumper-OCR temporal voting for track identity. Deliberately OpenCV-free so the PWA can mirror it in-browser; fully unit-tested.

**`tests/on_device_cv_reference.py`** (moved out of the app: nothing on the server runs it)
The OpenCV counterpart to `on_device.py`, kept separate so the core stays cv2-free. Provides Lucas-Kanade optical-flow camera stabilization (`StabilizedPose`): real-footage testing showed the field's AprilTags are unresolvable from stands distance, so instead of re-detecting tags every frame a scout taps the 4 field corners once and this module carries that base pose frame-to-frame via optical flow.

**`specs.py`**
Configuration for the scouting form itself — the form fields a draft can carry, valid value ranges, and season-specific priors for the derived insights.

## How It Runs

1. A scout records a match with the on-device recorder and it syncs to `POST /tracks/on-device-session`. An operator reviews the session and accepts it.
2. The scheduled `backfill.py` job (or an on-demand generate) finds accepted recordings with missing draft slots and calls `generate_auto_scout_drafts_for_match()` for the assigned teams. Without an accepted recording a draft fails with `no_on_device_recording`.
3. Offense and defense come from the shift-play analysis of that recording (defense only when it could be assessed).
4. Track-based insights are estimated — disabled period, zone dwell.
5. The draft is stored as `ready` or `low_confidence`.
6. A human scout reviews the draft, approves (with optional overrides), or rejects it.

## Dependencies

- `AnalysisRun` + `OnDeviceSession` — the accepted phone recording a draft is based on
- `game_config` — match timing parameters + the `shift_schedule` block used by `shift_play.py`
- `services/scheduler.py` — runs the backfill catch-up job
