# Dead code and inefficiency audit — 2026-10-05

`main` = `1e0b8c8`. About 49k lines of backend Python, 52.5k lines of frontend TS/TSX and 25k lines of global CSS were checked.

**Method.** Each finding comes from at least one of these sources:
- **Tools:**
  - `vulture` (Python), run with and without tests to separate dead code from code only tests use;
  - `knip` (TS exports, files, dependencies);
  - `jscpd` (copy-paste);
  - a CSS-class-to-source scan;
  - a design-token read scan;
  - a scan of backend routes against every caller in the repo (frontend, scripts, workflows, docker, docs).
- **Codex (gpt-6.1-sol, read-only):** two independent audits, one for the backend and one for the frontend, each proving references with `rg`.
- **Claude:** cross-checked the tool and Codex results, and spot-verified the high-impact claims in code and production config (marked ✔).

Sizes are approximate. "Removable" counts application lines; tests that only exercise removed code go with them.

## Status (2026-10-06): fixed

Everything below was acted on in two PRs (backend: dead code, retired features, migration `20261006_0019`; frontend: built with Codex gpt-6.1-sol, reviewed and checked in the browser). Kept on purpose:

- `GET /tracks/on-device-sessions` (operators need it to find recordings to accept) and the workspace admin list / join-code rotation (recovery).
- The ML shadow code and its tables: training and inference are switched off in production (`ML_SHADOW_AUTO_TRAIN_ON_EVENT_BREAKDOWN=false`, `ML_SHADOW_ENABLED=false`), so ML can come back once a model earns it. The ML badge in Predictions stays for the same reason.
- `auto_scout/on_device.py`, the documented Python reference of the phone's math (the demo script uses it). Its OpenCV twin moved to `tests/`.
- Nine e2e scripts that the docs reference; `on-device-benchmark.ts`.

Verified: ratings, synergy projections and season strengths rebuilt for 2026arc, 2026casac and 2026mndu from a production copy are identical between old and new code (155 ratings, 650 projections, 3,777 strengths, 0 differences), and the rebuild ran in about half the time.

---

## A. Safe to delete — nothing uses it (≈1,450 lines)

### Backend

| Where | What | Lines |
|---|---|---:|
| `api/routes_scouting.py:329–626` | Model-QA helper tree (`_build_event_match_truth_rows`, `_rank_stability_snapshot`, `_signal_quality_snapshot`, `_qa_*`): the model-QA feature was removed and these were left behind. vulture + Codex ✔ | ~298 |
| `api/routes_events.py:36, 313–371, 847–904` | `EVENT_SEARCH_AUTOBACKFILL_ENABLED = False` is a literal, so 4 backfill branches can never run. Their setup **still runs**: per-search count queries and a `missing_by_year` map. ✔ | ~116 |
| `api/routes_scouting_rooms.py` (9 sites) | Retry branches that need `_bootstrap_missing_room_table()` to return `True`, but it always returns `False` (✔ read). Keep the logging and the real `StaleDataError` retry. | ~80–110 |
| `services/ratings/model.py:26–109` | Re-exported helpers nothing imports through this module. The implementations stay in their own modules. | ~50 |
| `services/analysis/evidence.py:20–57` | `video_evidence()`: the broadcast-evidence builder (only a test calls it). | 38 |
| `db/models.py:222, 443–452` | `Artifact` ORM model and its relationship (the table stays until a migration decision). | 11 |
| `api/routes_teams.py:63–65, 599–610` | Duplicate `TeamCapabilityUpsertRequest`; `_statbotics_norm_epa_from_context()`. | 15 |
| `services/events/match_times.py:29`, `api/schemas.py:175`, `auto_scout/shift_play.py:243`, `scouting_rooms/bus.py:35` | `effective_start_time`, `SynergyPairBreakdown`, `scoring_present`, `instance_id`: definition only. | ~10 |
| `auto_scout/scouting.py:1213–1226` | Approved-draft conditions that can't be true (the function already returned). | 2 |
| `scripts/evaluate_win_probability_replay.py:159` | Patches an alias nothing calls (the next line does the real patch). | 1 |

**Used only by tests** (move into test helpers, or delete with their tests):
- `auto_scout/on_device_cv.py` (whole module, 75 lines; it is also the only reason `opencv-python` is in production `requirements.txt`);
- `analysis/runs.latest_completed_run` (21);
- `on_device.field_reference_corners` (6);
- `fuel_win_probability.fuel_margins_by_match` (7);
- `perimeter_resolver.normalize_perimeter_type` (7);
- `FieldCalibration` model (23; the table needs a migration decision);
- 6 test-only re-exports in `ratings/model.py`.

### Frontend

| Where | What | Lines |
|---|---|---:|
| `src/api.ts:2741–2938` | Old room-assignment calls `getScoutingRoomAssignments` / `upsertScoutingRoomAssignment` / `replaceScoutingRoomAssignments` and their response types. Replaced by the team-room flow (#111). knip + Codex ✔ | ~120 |
| `components/ui/SurfaceCard.tsx:20–23, 79–82` | `SurfaceCardGroup` is a no-op fragment wrapped around 34 call sites in 21 files (✔ read). | ~65–90 |
| `shell-shared.css:1902–2084`, `shell-foundation.css:4069, 5319` | Old `scouting-mobile-*` save/tab/history families (only `scouting-mobile-compact` is still used ✔). | ~167 |
| `ProductShell.css`, `shell-shared.css` | `ps-topbar-signature`, `ps-brand-signature(-link)`, `ps-context-strip-head/title`, `ps-panel`. | ~63 |
| `shell-features.css:1254–1300` | `compare-mobile-summary-*`. | ~40 |
| `shell-foundation.css:3946–3974` (+ selector arms) | `theoretical-*-grid`. | ~35 |
| various CSS | `center-progress-*`, `center-checkbox-label`, `compare-chip-row`, `favorites-overview-grid`, `assign-mobile-divider`, `fm-actions-row`, `coverage-summary-grid`. | ~62 |
| `shell-overrides.css:774–801` | Exact duplicate of `1010–1037` inside the same media block (keep the later one). | ~28 |
| `ScoutingPage.module.css:7–29` | `note`, `noteWarning`, `actions`, `chipRow`. | ~25 |
| `styles/tokens.css` | 41–43 custom properties never read (`--bp-*`, `--type-xs…2xl`, `--alliance-*`, `--shadow-xs/sm/inner/glow`, `--space-0/12/16`, …). ✔ | ~53 |
| `ProductShell.css:9`, `shell-shared.css:2749` | Unused keyframes `ps-spin`, `uiCardRiseIn`. | ~14 |
| `ScoutingPage.tsx` (several), `scoutingPage.helpers.ts:111` | `mobileHistoryPanel`: stored and validated, but no setter and no effect on rendering. | ~25 |
| `ComparePage.tsx:127–171`, `TeamCenterPage.tsx:279–299` | Climb-source objects parsed, then thrown away. | ~40 |
| `hooks/useAutoScoutDraft.ts` | `seasonSupport`, `approvedDraft`, unused return fields. | ~14 |
| misc | `updateWorkspaceSessionDetails`, `disabled={… && false}`, unused hook params, `src/assets/react.svg`, test-only `nextAssignment` / `readTutorialSeenMap` / `SEASON.name`; knip-unused exports in `eventCalendar.ts`, `offlineReady.ts`, `gameFields.ts`, `apiResponseStore.ts`, `robotThumb.ts`. | ~40 |

---

## B. Inefficient code worth fixing (verified)

1. **Every event refresh computes ratings and synergy twice** (`events/pipeline.py:88–120` → `ingest.py:758–789`). `refresh_event()` calls ingest with the default post-compute, which production sets to `EVENTS_INGEST_RUN_POST_COMPUTE=true` (✔ checked on the VM), then runs `_post_compute()` again. The 12 h automation and the full re-ingest (7¼ h) pay for it. Fix: ingest with `run_post_compute=False` inside `refresh_event`.
2. **Synergy rebuilds the same dependencies repeatedly** (`ml/synergy.py:633–639, 837–843, 1314–1340`). Season and event strength are computed by the orchestrator, then again by each pair function. Each event refresh also rebuilds season-wide priors and regression samples, so a 299-event run reprocesses the season 299 times.
3. **Phones download OpenCV (≈7 MB) for offline use while the recorder uses the local optical-flow tracker** (`offlineReady.ts:65–72, 134–156`; `opticalFlow.ts:329`). Native validation also requires it.
4. **Team-room poll can stack requests** (`useTeamRoom.ts:192–222`): a 5 s interval with a 15 s timeout and no in-flight guard (✔ read). The schedule poll ignores visibility. Use `useSingleFlightPolling`.
5. **Auto-draft polling** (`useAutoScoutDraft.ts:124–130`): fixed 3 s interval, ignores visibility, can overlap.
6. **Rating recompute makes about 3 extra finding queries per team** (`ratings/model.py:271–280`, `elite_robot.py`, `elite_detector.py`), and the elite output is never read (see D).
7. **Draft batching reloads the match and all robots' tracks six times** (`auto_scout/scouting.py:1146–1455`).
8. **Push alerts query membership and assignments inside the subscription × match loop** (`push/match_alerts.py:110–172`).
9. **Rating and synergy writes use per-row `db.merge()` and then re-read** (`ratings/model.py:816–835`, `ml/synergy.py:1235–1280`).
10. **Schedule `limit`/`offset` applied after loading the whole event** (`routes_matches.py:1349–1493`).
11. **Pending recordings: every 30 s sync check reads every saved recording body from IndexedDB** (`offlineStore.ts:115–146`); each save fires a notification that re-reads them all again.
12. **Favorites refreshes both tabs every poll, and its error backoff can't trigger** (it returns `undefined`, never `false`) (`FavoritesPage.tsx:195–493`).
13. **The API client's in-memory cache never evicts** (`api.ts:260, 1424–1443`). **IndexedDB pruning loads full bodies and opens the database once per deletion** (`apiResponseDatabase.ts:14–40`).
14. **PyTorch is imported at backend startup** through `ml.shadow` even though ML is off (`shadow.py:52–57`).
15. **Smaller:**
    - ML feature rows built per match when shadow inference is off (`routes_events.py:1251–1310`);
    - `planAutoAssign` sorts every candidate list per slot;
    - `scheduleRows.find` inside an indexed loop (`ScoutingPage.tsx:978–1009`);
    - every `SurfaceCard` adds its own `matchMedia` listener.

### Duplicated logic to merge (≈450 lines)

| Copies | Where | Lines saved |
|---|---|---:|
| Calendar derivation + grid/modal | `HomePage.tsx` ↔ `EventsPage.tsx` (39-, 25-, 21-line identical runs) | ~130–200 |
| Team-intel parsing | `TeamCenterPage.tsx:270–438` ↔ `ComparePage.tsx:122–238` | ~80–110 |
| Event-list merge ×4 | `eventSearchIndex.ts`, `eventCatalog.ts`, `HomePage.tsx`, `EventsPage.tsx` | ~55–65 |
| `cx()` ×6 | six components; `primitives/cx.ts` already exists | ~15 |
| Team media handlers ×3 | `api/teams/media.py:72–295` (jscpd: 19/29/30-line clones) | ~50 |
| Realtime handler | `scouting_rooms/realtime.py:397–428` ↔ `598–627` | ~25 |
| Least squares ×2 | `ml/synergy.py:38` ↔ `ratings/helpers.py:185` (note the different empty-input slope) | ~14 |
| Model / ingest blocks | `db/models.py:499–574`, `ingest.py:763–845`, `elite_robot.py` ↔ `elite_detector.py` | ~40 |

Overall copy-paste is low (jscpd: 1.1% of 73k lines). `Icons.tsx` (676 lines) is repetitive SVG boilerplate, but all 65 icons are used.

---

## C. Runs or exists, but nothing uses the result — your call (≈2,500–3,000 lines)

These aren't strictly unreferenced, so each needs a product decision before removal.

- **ML shadow pipeline** (`ml/shadow.py` 2,619 lines + training jobs). ML is off in production, yet every 12 h tick retrains shadow models and writes `ml_feature_snapshots`, the **largest table at 161 MB**. Options: stop scheduled training and keep the code for later, or archive it.
- **Auto-scout scoring predictors** (`predictors/round1_deterministic.py`, `round2_ml.py`, `auto_mobility.py`, parts of `scouting.py`; ≈780–930 lines). They need findings, events or throughput rows that phone recordings never produce, so drafts leave those fields empty. Only test fixtures feed them. Offense/defense come from shift-play and are unaffected.
- **Elite-robot analysis** (`analysis/elite_robot.py`, ≈390 lines): computed for every team on every recompute (≈3N extra queries) and stored in rating details, but nothing reads it.
- **Match-video metadata backfill** (`ingest.py:641–731`, `MatchVideo`): a leftover of the retired video pipeline. It costs up to 24 extra TBA requests per event, and nothing in the frontend reads it.
- **Scheduled jobs with no current input:**
  - the climb video-vs-official audit (every 6 h, with no new video findings);
  - the daily auto-scout training export (every new session is `training_eligible=False`).
- **42 backend endpoints with no caller in the repo.** Biggest groups:
  - all 5 Statbotics proxy routes (≈470 lines; the server calls Statbotics directly);
  - 7 game-config routes (≈112) and the config-only `event_labels` / `penalty_rules` / `core_metrics` JSON (≈150);
  - 6 `teams/stats.py` routes (≈185);
  - Zebra MotionWorks (no 2026 data exists);
  - match detail;
  - synergy precompute/projections;
  - event stats/team-statuses/insights;
  - automation event refresh;
  - auto-draft telemetry;
  - about 12 maintenance/ML admin routes (≈230).

  Some may be hand-run admin tools; anything not used from the terminal can go.
- **Legacy `v1.` signed-token decoder** (`core/security.py:150–212`). Every issuer now uses JWT, so it can go once any old tokens have expired.
- **Tracked e2e scripts nothing references:**
  - `e2e/ios/probe.mjs`;
  - `stress-context-edits`, `stress-scout-recovery`;
  - `style-diff`, `style-snapshot`, `token-snapshot`;
  - `validate-identity-flow`, `validate-native-shell`, `validate-offline-sync`, `validate-recording-sync`;
  - `e2e/lib/colour.mjs`.

  About 1,300 lines; one-off QA tools from earlier rounds. `scripts/on-device-benchmark.ts` stays (documented).
- **ML presentation in Predictions** (the ML badge and the "ML powered" stat) can't appear while ML is off.

## D. Checked and found to be used (don't remove)

- Every page is routed, no orphan TS module exists, and every npm dependency is used.
- Predictor registration goes through `@register` (not missing callers); `match_events` is written by official ingest.
- Regional automation and the Texas-key guard are live; the on-device review route is used manually (runbook).
- All 65 icons and every `Settings` field are read; benchmark tooling and model manifests are used by build/scripts.
- Local only, not in git: `ScoutingApp/e2e/polish/` (29 MB of old screenshots/probes, excluded via `.git/info/exclude`). It's safe to delete from disk.
