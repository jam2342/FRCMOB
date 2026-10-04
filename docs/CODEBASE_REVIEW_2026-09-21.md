# Verification and codebase review — 21 September 2026

Follow-up: the authorized cleanup is implemented; see
[cleanup results and verification](DEBLOAT_RESULTS_2026-09-21.md). The findings
below describe the pre-cleanup state.

The local software checks pass, including real ONNX inference in Chromium. That is
not yet proof that a phone produces accurate team-level scouting. Keep v2 deployed
and tracklet merging disabled until the remaining accuracy checks pass.

This is a review of the current dirty working tree at `e6c1b04`, including the local
September 7 repairs. No runtime code was changed, deleted, committed, or deployed
in this review. Source scans covered 306 Python/TypeScript/TSX files under
`backend/app` and `ScoutingApp/src`; reference checks also covered tests, scripts,
browser harnesses, route registration, and build output. Static scans identify
candidates, not a proof of the absence of all dead code. Media, generated code,
dependencies, credentials, and the Oracle directory were excluded from source review.

## What passed now

| Check | Result / limit |
| --- | --- |
| Backend suite | 676 passed initially; three local-HTTP tests were blocked by sandbox socket permissions. All six provisioning tests passed on rerun with local server access. Thus all 679 ordinary tests passed across the runs; five subtests also passed. |
| Backend lint | Ruff passed for app, scripts, and tests. |
| Frontend suite | 261 tests in 42 files passed. |
| Frontend lint / types / build | ESLint, TypeScript, and Vite production build passed. Every asset referenced by generated `dist/index.html` exists. |
| Browser | On-device setup heading and controls rendered; screenshot inspected. Real 896 ONNX inference passed in Chromium with Apple Metal-3 WebGPU: median 99.6 ms, p90 101.9 ms, approximately 10 fps, five measured iterations after warmup. |
| Models | v2 and v3 PT hashes still match the September 7 evaluation. The 896 ONNX hash matches the September 4 validation document. |
| Audit | Assisted training dataset still explicitly says `needs_review`. |
| Working tree | `git diff --check` passed. Existing changes preserved. |

Browser timings used a synthetic canvas, on a Mac with an iPhone layout profile.
They are not robot-detection accuracy, full-pipeline throughput, phone performance,
or thermal evidence. The headless check also finished, but used SwiftShader
(15.6 seconds/frame); it is software rendering and is not a hardware benchmark.
Both harness runs reported no unfiltered browser errors. The harness deliberately
filters some fetch/connection errors; no live backend was running for these runs.

Two opt-in PostgreSQL/Redis integration tests were skipped today. Docker's Colima
socket is absent, so no database containers were available. Their September 7
passes remain historical evidence, not fresh results. No full-match browser run,
production request, or fresh 224-image inference comparison was performed today.

Raw logs and inspected screenshot: `backend/media/codebase_review_20260921/`
(local ignored artifacts). Prior model evaluation and limitations:
[MODEL_REVIEW_2026-09-07.md](MODEL_REVIEW_2026-09-07.md).

## How to establish that it works in practice

These are proposed acceptance gates, not results already achieved:

1. **Release artifact:** require the exact 896 ONNX asset that `detector.ts` loads;
   verify its hash, successful fetch, input size, and a detection on a labeled real
   frame from the actual release build. Fix the filename mismatch below first.
2. **Independent label review:** complete the existing 200-frame packet and the
   train/validation audit required by `LABELING_STANDARD.md`. Assisted corrections
   are not a human acceptance. Do not use the locked holdout for training or label
   selection. v3's recall improvement also came with more false positives.
3. **Real match accuracy:** choose several independently annotated matches, including
   a different venue and handheld footage. Run the actual tracking settings and
   compare v2/v3 on the same clips. Count missed/false detections, identity switches,
   false track joins, correctly identified coverage, and zone/dwell error. Check
   calibration at withheld visible floor landmarks and align video/match clocks.
   Keep merging off until its identity errors are demonstrably acceptable. A
   successful render or sync is not evidence of correct offense/defense scores.
4. **Actual phone:** preload app/model, record a complete match offline on the target
   phone, reload it while offline, then reconnect and sync. Inspect the saved tracks
   and server rows; verify retrying the same session produces one session/track set.
   Measure full-pipeline analyzed fps, dropped samples, pose fallbacks, identity
   coverage, heatmaps, and sustained timing. A reasonable initial performance gate
   is at least 2 analyzed frames/sec throughout a full match; agree accuracy bounds
   before promoting a candidate. Phone thermals need a sustained run, not five
   inference timings.
5. **Integration:** rerun both opt-in tests against disposable PostgreSQL 16/Redis 7
   with current migrations, then check real client-to-server sync. Current browser
   mocks do not substitute for this.

## Findings, in recommended order

### 1. Release check inspects the wrong model — high confidence, correctness

`ScoutingApp/vite.config.ts:14` checks
`public/models/frc_robot_detector_v2.onnx`. The actual default URL at
`ScoutingApp/src/features/onDevice/detector.ts:45` is
`/models/frc_robot_detector_v2_896.onnx`. With only the old file present, even
`ONDEVICE_MODEL_REQUIRED=true` can pass while the runtime asset is absent. With
only the correct new file present, it can reject a valid build. Both are present
locally, masking this mismatch. The guard also inspects `process.env` before
explicitly loading Vite env-file values, while client env loading happens later.

**Recommendation:** share a small model artifact definition between runtime and
build validation, resolve build-time environment consistently, and verify the
actual selected file. Cover old-only, new-only, neither, and explicit remote URL
cases. A remote URL being present is not proof that the remote asset is reachable.

### 2. Browser harness can report success without proving match correctness

`ScoutingApp/e2e/validate-on-device.mjs:66` mocks the match and sync endpoints.
At line 200, tracks receive team identities by `index % optionValues.length`.
At line 203, sync is skipped when zero tracks are produced. Final `ok` is based
on filtered browser errors, not positive track count, identity correctness, or a
real persisted server result. With no video, `fullMatch` is null and `ok` can still
be true, as in today's inference smoke test.

**Recommendation:** retain this as a UI/inference smoke harness, label its scope,
and add separate explicit assertions for a required full-match mode. Use known
identity annotations and real isolated backend persistence for an accuracy/sync
test. Do not interpret the current success flag as end-to-end scouting accuracy.

### 3. Lazy pages are pulled into startup chunks — high confidence, delivery bloat

`ScoutingApp/src/RootApp.tsx:8` onward declares lazy routes, but manual chunk rules
in `ScoutingApp/vite.config.ts:46` interact with shared dependencies. The actual
generated entry statically imports `page-events` and `page-scouting`, and scouting
statically imports `page-match-center`. `dist/index.html` also module-preloads them.
Their combined emitted size is about **437 KB uncompressed** (approximately
122 KB summing individually gzipped chunks), even before visiting those routes.

**Recommendation:** inspect shared dependency placement and compare a build with
automatic page splitting or explicit shared chunks. Verify startup requests and
per-route requests before accepting the change; extracting components alone does
not guarantee a smaller download. This is a stronger performance opportunity
than shaving a few helper lines. No bundle reduction has been implemented or
measured after a refactor yet.

### 4. Tutorial data and checklist subsystem have no current UI consumers

`ScoutingApp/src/tutorial/tutorialBlueprints.ts:17` defines checklist metadata;
its nine blueprint entries contain 146 lines of checklist data, 45 lines of
adoption playbooks, and 27 lines of subtitle/icon/estimated-time properties.
The sole production consumer, `components/tutorial/TabTutorialOverlay.tsx:145`,
uses the walkthrough, scope, and title, not those properties. Unused text such as
the checklist drill-down instruction still appears in the emitted entry bundle.

`tutorial/tutorialState.ts:120` onward exposes checklist reading, toggling, and
counting used only by tests (one extra test mock also names the count function).
Settings still calls `clearTutorialChecklist`, so removing the entire state file
would be wrong: seen-tutorial state is live, and legacy checklist cleanup remains.

**Recommendation:** remove unused metadata and test-only checklist behavior if
the checklist feature is retired. Preserve seen-state behavior and reduce legacy
reset to removing its storage key. This can remove a few hundred source lines;
exact gzip savings require a before/after build. Confidence: high for present
non-use; feature retirement is a product choice.

### 5. Dead ML helpers coexist with active copies

`backend/app/services/ml/model_eval.py:63`, `:74`, and `:143` define `r2_score`,
`expected_calibration_error`, and `summarize_distribution`. No callers were found
in app, scripts, or tests. Meanwhile `ml/shadow.py:979` and `:1000` implement and
call private versions of the first two.

**Recommendation:** use the shared evaluation functions from shadow and remove
the redundant private implementations, preserving the current float-input
behavior with focused numerical parity tests. Remove the unused distribution
helper unless there is an external consumer. Do not remove all of model_eval:
its loss, error, rank, and split utilities have live callers. Confidence: high
within this repository.

### 6. Identical live/training coverage code should have one implementation

`backend/app/services/auto_scout/scouting.py:286` and `training.py:59` contain
identical `_approx_track_coverage` function bodies. Their duration helpers also
perform the same config lookup/fallback. Five `_safe_float` implementations have
identical bodies across scouting, training, and three predictors.

**Recommendation:** extract the shared coverage calculation and identical finite
number conversion into a small dependency-safe module. This reduces the chance
that live confidence and training sample selection drift apart after a future
fix. Test empty tracks, sparse endpoints, nonfinite values, and season duration.
Do not blindly consolidate every similarly named number converter: other modules
have different defaults/contracts. Confidence: high; function-body AST comparison
confirmed these duplicates.

### 7. Four game-field exports have no references

`ScoutingApp/src/config/gameFields.ts:12` / `:13` (`SEASON_YEAR`, `SEASON_NAME`),
`:92` (`ENDGAME_MODES`), and `:102` (`RP_FLAGS`) appear only at their declarations
in the checked source/test/harness corpus.

**Recommendation:** remove the unused exports or wire a single shared schema into
the real form controls if that was the intended design. They are misleading as
an apparent source of truth that the form does not consume. Small, low-risk source
cleanup; unused exports may already be tree-shaken, so do not promise runtime gains.

### 8. Room leader actions repeat a large lifecycle

`ScoutingApp/src/pages/ScoutingPage.tsx:2388` and `:2430` repeat room/authority
validation, target normalization, token acquisition, pending state, the same
secondary-leader state update, and error/finally handling. Kick at `:2348` shares
some scaffolding but has important distinct self-removal and response behavior.

**Recommendation:** consolidate promote/demote behind a typed action helper or
focused room-members hook. Keep kick's distinct checks explicit. Verify owner
denial, expired tokens, successful promotion/removal, API failure, and pending-state
cleanup. This is a concrete way to shorten the 4,623-line scouting page without
turning it into a generic framework. Confidence: high; medium regression risk.

### 9. Ratings orchestration remains unusually coupled after extraction

`backend/app/services/ratings/model.py:130` has a 747-line orchestrator;
`ratings/feature_extraction.py:52` has an 837-line feature function. The rating
weight list is repeated at model.py:169 and :628. The paths are not identical:
the final path also applies blending, penalty deduction, and sparse-rating guards.

**Recommendation:** first centralize the weight specification and pure base-score
calculation, preserving the order of final adjustments. Then extract cohesive
feature groups with typed results and parity fixtures. A wholesale rewrite aimed
only at line count is high risk. Compatibility re-exports near model.py:26 are
not all dead: routes, helpers, and tests still import through this module.

### 10. Superseded model asset adds unnecessary release size

The local `ScoutingApp/public/models` contains both the old 640 model
(37,888,387 bytes) and current 896 model (38,049,917 bytes). Vite copies public
assets to dist. Current default inference uses only 896; the older artifact is
documented as superseded in `ON_DEVICE_VALIDATION.md:15`.

**Recommendation:** stage only the selected artifact in releases and retain older
models outside public assets for reproducible experiments/rollback. Check external
URL consumers before removing an existing hosted URL. This saves about 37.9 MB
of release files locally; it is not necessarily a 37.9 MB initial-page download.
Fix the build guard before removing the old local file.

## Things deliberately not called dead

- Lazy page components and ErrorBoundary/PageSpinner: active through RootApp/JSX.
- Both offline sync starters and event-index preloading: called in `main.tsx`.
- `benchmark()`: called by the browser harness, not ordinary UI imports.
- Predictor modules: registered dynamically; a static import graph misses them.
- Python on-device geometry/CV: used by tests/scripts and as browser parity reference.
- OpenCV fallback: still selectable and tested, so not safe to delete just because
  local optical flow is the default.
- Ratings compatibility exports, migrations, CLI scripts, and recovery fallbacks:
  no deletion recommendation based only on low reference counts.

Recommended sequence: correct release validation and make test claims precise;
repair startup chunking; remove the confirmed unused metadata/helpers/constants;
consolidate coverage and room actions; refactor ratings only with output-parity
fixtures. Keep cleanup separate from model promotion so failures remain diagnosable.
