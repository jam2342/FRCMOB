# Independent second-pass codebase audit — 27 September 2026

Scope: read-only audit of `Scouting app` at `65110f7`, following `CLAUDE.md`, `docs/REBUILT_PLAUSIBILITY.md`, and the first audit in `docs/CODEBASE_AUDIT_2026-09-27.md`. This report is the only repository file written. No production requests, code changes, credentials, or locked holdout data were used.

## Coverage and verification

Read line by line: `api/routes_tracks.py`; `vision/sparse_association.py`, `vision/tracklet_merge.py`, `vision/bumper_ocr.py`; the PWA on-device capture, calibration, sync, storage, tracker, detector, geometry, identity, and track-production modules. Read substantial relevant paths in `auto_scout/shift_play.py`, `auto_scout/training.py`, `ml/shadow.py`, `vision/tracking_detection.py`, `services/jobs.py`, `api/routes_events.py`, and the API client. Scanned related config, tests, rating features, and call sites. This is a focused second pass, not a line-by-line audit of the entire repository; notably, large unrelated pages, migrations, and most API routes were not revisited.

Local checks: `pytest -q tests/test_shift_play.py tests/test_routes_tracks_on_device_sync.py` passed **34** tests; `vitest run` on the on-device tracker, sync, and run tests passed **12**. Separate local scratch repros exercised the actual sync function, route, ML dataset preparation, tracker, and shift-play engine. Passing existing tests do not cover the failures below.

## High severity

### H1. A workspace switch can upload queued sessions into the wrong team's workspace

**Code:** `ScoutingApp/src/features/onDevice/sync.ts:22-43` captures `workspaceId` once and passes `postSession` to a multi-item flush. `offlineStore.ts:122-136` awaits each POST in sequence. `ScoutingApp/src/api.ts:1712-1715` reads the *current* workspace token for every request; `backend/app/api/routes_tracks.py:748-755,924-930` assigns the received session to that token's workspace.

**Repro:** Queue two sessions with `workspaceId=1`. Start a flush under workspace 1, let the first POST wait, switch the active UI workspace to 2, then release the first POST. A local run of the real `flushPendingOnDeviceSessions` with delayed `fetch` sent `first` with `token1` and `second` with `token2`; both were originally workspace-1 sessions. The second is then marked synced locally, so it will not be retried for workspace 1. The same risk exists for pre-workspace sessions queued for the workspace at flush start.

**Impact:** Cross-team disclosure and permanent misfiling of a scout's match data during an ordinary workspace switch or logout/login while an offline queue drains.

**Fix:** Bind a token and workspace identity to each flush and pass that token explicitly to every POST; recheck identity before each item and stop the flush on change. Validate the session's workspace at the server boundary where possible. Add a two-session switch test.

### H2. Retrying a reviewed on-device session erases the admin decision

**Code:** The existing-session path in `backend/app/api/routes_tracks.py:863-891` deletes the run's tracks, rewrites its metadata, and unconditionally sets `status = "provisional"`, `review_note = None`, and `reviewed_at = None`. Review is an admin action at `routes_tracks.py:1078-1099`; ordinary team members can sync.

**Repro:** In a local SQLite `TestClient` run, sync a session (200), accept it through the review endpoint (200), then POST the same client session ID again (200, `reused_run=true`). The database row ends as `provisional` with both review fields null. A rejected session is equally vulnerable; the uploaded track rows are replaced too.

**Impact:** An offline retry, malicious replay, or repeated user action can undo an admin's accept/reject decision and replace evidence without review. The client session ID currently deduplicates runs, not reviewed content.

**Fix:** Make exact retries idempotent and immutable after review. For changed payloads, reject with a conflict or create a new provisional revision while retaining the reviewed snapshot and audit fields. Test both accepted and rejected replays.

## Medium severity

### M1. The shadow “match outcome” classifier learns fuel throughput, not the official winner

**Code:** `backend/app/services/ml/shadow.py:377-423` sums `TeamMatchThroughput.active_bps` for each alliance and sets `"red_win": 1.0 if margin > 0.0 else 0.0`. `shadow.py:1262-1294` trains the match-outcome classifier against `red_win`. When the optional shadow blend is enabled, `backend/app/api/routes_events.py:1269-1335` presents its output as `red_win_prob` in the schedule prediction.

**Repro:** The target builder returns `red_win=1` for red active throughput 1.2 versus blue 1.1, regardless of a final official score such as red 100, blue 110. Climb and penalties can change the actual winner. This is a semantic label error, independent of classifier accuracy. The public impact is conditional on `ml_shadow_enabled` and a positive outcome blend; current defaults disable that path.

**Impact:** Model validation measures a proxy task while the UI can call it win probability. Training and activation can favor a model that is wrong about match outcomes.

**Fix:** Build outcome labels from final official scores, excluding unplayed matches and defining tie handling. If throughput advantage is a useful separate target, name and evaluate it separately and never expose it as a win probability.

### M2. Rows tagged `holdout` are included in shadow training

**Code:** `backend/app/services/ml/shadow.py:453-456,525-536` tags the latest year `holdout`. But `_prepare_dataset` at `shadow.py:784-821` loops over all snapshots without reading `snapshot.split_tag`, then creates its own 80/20 time split. Match outcome training invokes it at `shadow.py:1279-1294`; other shadow trainers use the same helper.

**Repro:** A local call with 20 valid fake snapshots, all tagged `holdout`, returned 16 training rows and 4 validation rows instead of rejecting the empty training pool. This concerns `MLFeatureSnapshot.split_tag`, not the separate protected detector holdout dataset.

**Impact:** The named holdout set is exposed to fitting and tuning. Reported validation cannot establish the intended out-of-year generalization.

**Fix:** Partition by `split_tag` before fitting; use a time split only inside the training partition. Keep holdout untouched until a final one-time evaluation, and fail clearly when no training rows remain.

### M3. A calibration can be applied to a different camera view before stabilization begins

**Code:** `ScoutingApp/src/features/onDevice/FieldCalibration.tsx:78-92,164-184` computes a homography in the uploaded/still frame's pixel coordinates and saves only the matrix and quality fields under global ID `current`. `OnDeviceRun.tsx:253-265,322-335` reuses that matrix for a later capture. `MatchRecorder.tsx:182-205` opens a new camera stream. The resolver at `opticalFlow.ts:252-272` initializes from the saved matrix and returns it unchanged for the **first** recording frame; it only measures motion between subsequent frames.

**Repro:** For a synthetic four-corner calibration, a point at x=400 px mapped to field x=8 m. If the phone pans 100 px before recording starts, the same physical point appears at x=500 px and the unchanged homography maps it to x=10 m. A changed capture resolution causes the same class of error. Four-tap RMSE remains excellent because it measures the earlier image, not registration to the new one.

**Impact:** Zones, heatmaps, and shift offense/defense can be displaced while the run still reports a verified calibration. Saved `current` calibration can be reused after the phone moves.

**Fix:** Keep the calibrated camera stream and coordinate system continuous into recording, or register the first recording frame to the calibration frame and normalize dimensions before accepting points. Save reference image dimensions and reject/re-tap when first-frame registration fails; scope stored calibrations to a capture geometry/view.

### M4. Camera motion fragments PWA tracks before field stabilization is applied

**Code:** `ScoutingApp/src/features/onDevice/OnDeviceRun.tsx:383-402` calls `assignTrackIds(rawFrames)` before `produceTrackPoints(frames)`. `simpleTracker.ts:27-77` links floor-contact points using a fixed 150 px gate in raw image coordinates. The per-frame homographies are applied only afterward. Tracks shorter than `MIN_TRACK_POINTS` are discarded.

**Repro:** In a local run of the actual tracker and track-production functions, one stationary robot's box shifted 160 px during a camera pan while the next homography compensated the pan. Both projected field positions were about `(2.4, 2.8)` m, but the tracker assigned IDs `[0, 1]`. Repeated pans create short fragments that the run drops.

**Impact:** The optical-flow pose path can succeed while association, identity voting, and heatmap coverage fail from avoidable fragmentation.

**Fix:** Project floor contacts into field coordinates before association and gate by plausible robot speed and sample interval. Alternatively warp all detections to a common stabilized image frame before pixel matching. Test a pan larger than 150 px with a stationary field robot.

### M5. Adaptive video sampling can truncate the final seconds of a 160-second match

**Code:** `backend/app/services/vision/tracking_detection.py:333-340` computes `required_frames = ceil(duration / interval) + 1`, then caps adaptive mode at `min(300, ceil(max_frames_setting * 1.5))`. `services/jobs.py:635-638,684-739` uses this plan; `vision/video_extraction.py:475-480` limits extracted frames to the cap.

**Repro:** With `duration_sec=160`, adaptive interval/minimum of 0.5 s, and `video_tracking_max_sample_frames=320`, `_resolve_sampling_plan` returns `required_frames=321` but `max_frames=300`. Regular sampling ends at about 149.5 s, omitting roughly 10.5 s of endgame. This requires the adaptive minimum to allow 0.5 s; it is a configuration-dependent risk, not a claim that every current deployment truncates video.

**Impact:** Late climb and last-shift evidence can vanish from otherwise completed analyses with no explicit truncation warning.

**Fix:** Ensure the cap covers the full requested duration, or increase the interval across the *whole* match to fit the budget. Report the actual covered time and fail or warn when it falls short.

### M6. OCR's “max samples per track” selection skips the tail of a track

**Code:** `backend/app/services/vision/bumper_ocr.py:223-233` sets `bucket_size = n // max_samples_per_track` and iterates only through `min(n, max_samples_per_track * bucket_size)` before taking up to the maximum. With the configured default 12 samples, 23 rows select from indices 0–11 and never inspect 12–22; 35 rows choose among indices 0–23 and never inspect 24–34.

**Repro:** Give a 23-frame track unreadable early bumper views and a clear number in its last 11 frames. The selector never passes those late frames to OCR, so temporal voting cannot recover the identity.

**Impact:** Robot identity remains unresolved even when a clear bumper appears later in the same track; this weakens downstream auto-scout and team metrics.

**Fix:** Divide the entire index range into up to `max_samples_per_track` near-equal buckets, selecting the best-confidence frame from each. Test odd track lengths just under and over twice the sample budget.

### M7. Shift dwell at a boundary is credited entirely to the earlier shift

**Code:** `backend/app/services/auto_scout/shift_play.py:149-159` assigns each point the time until the next point (capped), and `shift_play.py:291-304` puts the full interval in the shift containing the *starting* point.

**Repro:** A red robot point at 54.9 s followed by one at 56.9 s yields 2.0 s under `shift_1` and 0.0 s under `shift_2` in `analyze_robot_shift_play`. The 55 s boundary means the correct allocation is 0.1 s to shift 1 and 1.9 s to shift 2. The analogous error occurs at 30, 80, 105, and 130 s.

**Impact:** Attack/defense dwell, zone time, and inferred role can flip near the alternating-hub boundaries, particularly at sparse sampling rates.

**Fix:** Split each dwell interval at every crossed shift boundary, applying each piece to its own window. Preserve the existing cap while splitting.

### M8. “Defense assessable” does not require simultaneous opponent tracking

**Code:** `backend/app/services/auto_scout/shift_play.py:368-380` sets `defense_assessable` when there is any self tracking during opponent-active time and `opp_index.has_data()`. The latter means only that an opponent has *some* points (`shift_play.py:179-180`); it does not require temporal overlap.

**Repro:** With a red robot tracked at 60–61 s and the blue opponent tracked only at 1–2 s, `analyze_robot_shift_play` reports `defense_assessable=True`, `opponent_tracks_available=True`, and positive defense confidence, although there is no frame on which opponent proximity or throughput disruption can be assessed.

**Impact:** A numeric defense level can look evidence-backed even when all opponent observations are outside the relevant shift.

**Fix:** Compute simultaneous opponent coverage within the robot's opponent-active intervals; require a minimum overlap to mark defense assessable and derive confidence from that overlap. Mark the defense estimate unavailable when coverage is zero.

## Corrections and extensions to the first audit

No still-current first-audit finding was disproved in this pass. Several items were **understated**:

- Section 2 describes competing match-prediction systems. The shadow target itself is mislabeled: `shadow.py:377-423` trains “win” on active-throughput leader, even before comparing the systems (M1).
- Section 3 correctly says synced on-device sessions are provisional and lack a user-facing review path. It does not cover the stronger integrity failure at `routes_tracks.py:863-891`: a normal retry can reverse an admin review and replace its tracks (H2).
- Section 3's on-device visibility concern also misses the cross-workspace queue race (H1). A successful upload can be visible to the *wrong* workspace and disappear from the intended team's local pending queue.

The first audit's listed fixes that subsequently landed in PRs #54/#55 are historical context, not repeated findings here. Their current status should be checked against the commit under review before using the first report as a live defect list.

## Recommended fix order

1. **H1:** Pin workspace identity during offline sync; test a switch during a two-item flush.
2. **H2:** Make reviewed sessions immutable or revisioned; test replay after accept and reject.
3. **M3/M4:** Register the first capture frame to its calibration, then associate robots in stabilized coordinates. Verify with a pan and resolution change.
4. **M1/M2:** Correct shadow outcome labels and enforce the named holdout partition before any model activation or validation claim.
5. **M5/M6/M7/M8:** Close the remaining evidence-quality gaps with full-duration sampling, whole-track OCR sampling, boundary-split dwell, and overlapping-opponent coverage tests.
