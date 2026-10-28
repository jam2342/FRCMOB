# Cleanup results — 21 September 2026

Implemented the ten cleanup findings in `CODEBASE_REVIEW_2026-09-21.md` locally.
Existing detector/data-safety work was preserved. No commit, push, deployment,
model promotion, or paid compute was performed.

## Measured changes

| Measure | Before | After |
| --- | ---: | ---: |
| Entry JavaScript and its static imports/preloads | 723,432 bytes | 398,973 bytes |
| Sum of individually gzipped entry/import files | 208,609 bytes | 117,766 bytes |
| Public ONNX artifacts copied to release | 75,938,304 bytes | 38,049,917 bytes |

The initial static JavaScript graph is **44.8% smaller** (gzip **43.5% smaller**).
These figures exclude the route subsequently selected, CSS, and runtime requests;
they are not a claim about total application download or measured time-to-interactive.
Application source under `backend/app` and `ScoutingApp/src` has **485 fewer lines**,
including new shared modules and excluding tests. Build tooling/tests/docs are not
included in that line count.

The first browser check exposed an additional idle-prefetch hook: it downloaded
seven routes even after fixing manual chunking. Removed that hook and its call.
The final browser check confirms Events, Scouting, Match Center, and on-device
recording are not downloaded on Home and do load when visited.

**Tradeoff:** the first visit to a new route fetches its JavaScript at that point.
Visit the screens needed at an event while online; unvisited routes are no longer
automatically warmed for offline use. This cleanup is not an offline-preparation
feature or a guarantee that model files are cached.

## Changes by finding

1. **Model release validation:** browser and build share `modelArtifact.ts`. The
   build plugin validates the selected local file and uses resolved Vite env-file
   settings. Required builds fail for missing/old-only models. An explicit HTTP(S)
   URL is accepted as configuration, not certified as reachable by the build.
2. **Browser harness:** results identify `inference-smoke` or `workflow-smoke`,
   identity source, and explicitly mark server persistence/scouting accuracy as
   unverified. `ONDEVICE_REQUIRE_FULL_MATCH=true` fails without both video and
   calibration inputs; a supplied workflow fails for zero tracks, missing sync,
   or an empty point payload. Optional `ONDEVICE_TRACK_IDENTITIES` JSON maps track
   IDs to reviewed team keys. Synthetic assignments remain available only as
   clearly labeled workflow fixtures.
3. **Bundle loading:** removed the custom manual chunk rules and indiscriminate
   idle-prefetch hook. Vite splits dynamic routes without putting their code in
   the initial static graph.
4. **Tutorials:** removed unused checklist/playbook metadata and checklist state
   functions. Tutorial walkthroughs, seen-state behavior, and removal of the legacy
   checklist storage key during Settings reset remain.
5. **ML metrics:** shadow models use shared R²/ECE implementations; duplicate private
   copies and unused distribution summarization were removed.
6. **Auto-scout helpers:** shared conversions, rounding/clamping, season duration,
   and track coverage replace repeated implementations. Existing NaN/infinity and
   sparse-endpoint semantics were deliberately preserved; this is not a new input
   validation policy.
7. **Game fields:** removed the four unused season/endgame/RP exports. Live form
   configuration remains intact.
8. **Room controls:** promote/demote share one typed action handler; authorization,
   token acquisition, separate pending indicators, response messages, error cleanup,
   and distinct kick behavior are preserved.
9. **Ratings:** one base-weight calculation serves shadow seeds and final scoring.
   Penalties, confidence shrinkage, blending, and sparse guards retain their order.
   Match-event scoring aggregation moved into a typed `ScoringEvents` result in
   `ratings/event_features.py`, reducing the main feature-extraction function by
   roughly 90 lines. The rest of the large ratings pipeline was not rewritten for
   cosmetic line-count gains.
10. **Old model:** archived the 640 artifact at
    `backend/media/models/archive/frc_robot_detector_v2_640.onnx`, verifying SHA-256
    `babd760102444199075a59a9a5754d07bdc1c1a225ab4ee40ff7c2f853904430`
    before removing the public copy. Final dist contains only the current 896 model.
    No hosted file was removed.

## Verification

- Backend: Ruff passed; **690 tests passed, 2 integration tests skipped, 5 subtests
  passed**. PostgreSQL/Redis opt-in tests were not rerun; Docker was unavailable in
  the preceding review. This work does not claim a fresh database integration pass.
- Ratings: golden fixtures captured by executing the pre-cleanup implementation
  against isolated SQLite data. Sparse and mixed scoring/penalty/climb fixtures
  match the refactored feature values, confidence inputs, and API ratings exactly;
  only runtime/write timestamps are excluded. ML/external EPA fetching is disabled
  in those fixtures. Separate known-value checks cover the shared base weights.
- Frontend: **265 tests in 43 files passed**; ESLint, TypeScript, and production build
  passed. The release build ran with `ONDEVICE_MODEL_REQUIRED=true` and the current
  local 896 URL. Model-guard cases cover absent, old-only, new-only, local override,
  remote override, and Vite env-file configuration.
- Room actions: rendered the real Scouting page, joined a fixture room, promoted
  and removed a leader, and verified both error paths restore usable controls.
- Production browser: Home, Events, Scouting, Match Center, and on-device recording
  rendered with API fixtures and no page errors; deferred route requests verified.
  Scouting screenshot inspected. This is UI verification, not live backend evidence.
- Service-worker check: after visiting and reloading Scouting online under the
  active worker, the same screen successfully reloaded offline. This verifies a
  previously cached route, not an unvisited route or offline model availability.
- Real ONNX inference after cleanup: Chromium/Apple Metal-3, 896 model, five measured
  iterations after warmup: median **96.6 ms**, p90 **100 ms**, **10.35 fps**. Synthetic
  canvas on a Mac, not phone thermals or real-match accuracy. Harness correctly
  reports `scope: inference-smoke` and both verification flags as false.
- Full-match-required preflight correctly exits nonzero when inputs are missing.
  No full-match video was available in the local media tree, so the changed
  full-workflow path has not been replayed with real footage in this cleanup.
- `git diff --check` passed. Raw logs, bundle graph, browser results, and screenshots
  are stored locally under `backend/media/debloat_20260921/` (ignored by git).

Physical-phone accuracy, label acceptance, annotated identity continuity, and model
promotion gates from the earlier review remain outstanding. None is implied by
this cleanup's test results.
