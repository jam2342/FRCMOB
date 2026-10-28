# Codebase audit — 27 September 2026

Design errors, contradictions, disconnected or duplicated systems, and bloat across
`backend/`, `worker/`, `ScoutingApp/`, `docker/`, CI and docs. No code was changed by
this audit. Severity: **H** = wrong result, broken feature or data risk today;
**M** = two systems that disagree, or a design that will bite; **L** = cleanup.

## Status on 4 October 2026

Every item below was re-checked against `main`. "This round" is the audit-leftovers PR
that adds this section.

| Item | Status |
|---|---|
| 1.1 Alliance scorer / auto drafts admin-gated | Fixed (#54); auto drafts are admin-only (#55) |
| 1.2 Live data cached 300 s | Fixed (#54, live endpoints 15 s) |
| 1.3 Offline picklist conflicts dropped | Fixed (#54) |
| 1.4 Room cleanup deletes assignments | Fixed (#54) |
| 1.5 Statbotics client across event loops | Fixed (#54) |
| 1.6 Anonymous GET creates fake events | Fixed this round: a TBA 404 now returns 404 and creates nothing; the TBA client no longer retries 4xx answers (it backed off three times on every 404) |
| 1.7 Event-teams intel blocks the loop | Fixed (#54) |
| 1.8 Auto-scout empty drafts | Moot: broadcast video retired (#57); drafts come from accepted on-device sessions; auto mode admin-only |
| 1.9 Last member leaving doesn't lock | Fixed (#54) |
| 1.10 Detector not cached offline | Fixed (#55, offline packs #71–#76) |
| 1.11 Tour prompt under the tab bar | Fixed (breakpoint now 1120 px) |
| 2.1 Three win-probability models | Fixed (#57, one answer); now official fuel rates (#101) |
| 2.2 Two "fuel/min" definitions | Fixed (#57, per active-hub minute everywhere) |
| 2.3 Team Center shows rejected rows | Fixed this round: rejected findings are never shown in their place |
| 2.4 Scout rating on old-game scales | Recalibrated (#55); still computed on both sides |
| 2.5 / 2.7 Match time meaning, push on schedule | Fixed (#57, predicted/actual times stored and used) |
| 2.6 Two schedule endpoints | Open by design: live results vs synergy + win probability |
| 2.8 Two "my team" systems; 2.9 two alliance-selection systems | Open: product decisions |
| 2.10 Two coverage formulas | Left: neither score is shown anywhere |
| 2.11 Rank fallbacks / invented EPA | Invented EPA removed (#55); model rank fallback is labelled `model_rating_fallback` |
| 2.12 Four team-key normalizers; 2.14 shift timing defined twice; 2.19 labels/thresholds | Open (cleanup) |
| 2.13 "Current season" sources | Fixed this round: one `regional_automation_season()`, falling back to the game config, never the calendar year |
| 2.15–2.17 Provenance, two enqueue systems, two media deleters | Moot: removed with the video pipeline (#57) |
| 2.18 Capacity utilisation constant 0.8 | Open: no capacity data exists; changing it moves every rating |
| 3.1 On-device sync invisible | Fixed (#57: accepted sessions feed heatmaps, Attack vs Defense, drafts) |
| 3.2 Elite/role computed, unread | Open: still computed on every recompute (whole recompute ~0.3 s) |
| 3.3 Intel snapshots nobody reads | Fixed this round: built with the parameters pages request (it used admin-only `auto_heal_ratings=true`, ~1 min of work every 2 min for nothing), and fresh values now win over the previous snapshot (snapshots used to freeze at their first build) |
| 3.4 Model-QA subsystem dead | Fixed this round: route and helpers removed (~930 lines) |
| 3.5 Unreachable code (appendix) | Removed (#57); last helper (`_require_scout_profile`) removed this round |
| 3.6 Routes without a frontend caller | Open: many are admin/ops tools |
| 3.7 Sparse fallback rating | Fixed this round: removed; it scored REBUILT numbers on old-game constants, so every team with data read ~72+ |
| 3.8 Dead config; snapshots never pruned | Snapshots: fixed this round (written only on change, trend reads the last 12 per team instead of every row, pruned daily at 30 days; 2026cmptx alone had ~112k rows). Dead config: open |
| 4.1 Percentile ties | Fixed (#54) |
| 4.2 GET triggers recompute | Fixed this round: off by default, admin-only |
| 5 Performance | Team search no longer calls TBA per result by default (this round). Room endpoints doing sync DB work on the loop, and the N+1 queries: open |
| 6 Names, concurrency, backups | Unique names and write locks (#79); stale `backup_postgres.sh` deleted this round; daily archive of pit photos + ML artifacts added to the backup (this round; same disk, so not off-site). The always-False `_bootstrap_missing_room_table` stays: it performs the rollback its callers' fallbacks depend on |
| 7 Duplication | Open |
| 8 Frontend | Home empty state, `/primitives` and SW cache versioning fixed; UI guards still not in CI |
| 9 Infra/docs | Docs updated; `alembic check` in CI (#61); CI on PostgreSQL 17 (this round); ruff still skips `scripts/` |

## How this was done

- **Read line by line:** backend `core/`, `db/`, `main.py`, `api/routes_teams.py` + `api/teams/*`,
  `routes_scouting.py`, `routes_scouting_rooms.py` (parts), `routes_events.py`, `routes_matches.py`,
  `routes_picklists.py`, `routes_pit_scouting.py`, `routes_push.py`, `routes_workspaces.py`,
  `routes_game_config.py`, `routes_auto_scouting.py`, `routes_synergy.py` (helpers);
  services `workspaces`, `scouting_rooms/*`, `quality_gate`, `analysis/{evidence,pipeline_quality,runs,hash}`,
  `calibration/evidence`, `scoring/official_team_stats`, `season_config`, `game_config`, `intel/snapshots`,
  `cache`, `utils`, `media/retention`, `push/match_alerts`, `tba/client`, `clients/statbotics` (parts);
  frontend `utils/offlineQueue.ts`, `public/sw.js`, routing, storage, breakpoints; `docker-compose.oci.yml`,
  Dockerfiles, `worker.py`, CI workflows, docs.
- **Whole-repo scans:** every backend route vs frontend callers (156 routes); every frontend write vs the
  admin-exempt list; async handlers calling blocking code; duplicate function bodies (Python and TS);
  call-graph reachability for dead Python; unused TS exports; CSS selector duplication and breakpoints;
  browser storage keys; stale infra references in docs.
- **Verified live on production:** alliance scorer 403 for non-admins; tour prompt covered by the tab bar at
  1024 px; Home match-card overlap (fixed in the working tree today). **Reproduced locally:** the
  Statbotics cross-event-loop failure and the percentile tie bug.
- **Not read in depth** (scanned only): `vision/*` internals, `auto_scout/*` internals, `ml/shadow.py`,
  `jobs.py` internals, `ratings/feature_extraction.py`, `features/onDevice/*`, and the inside of the large
  pages (Scouting, Events, Home, Team Center, Compare, Match Center). Findings below that touch these came
  from targeted reads, not a full pass.

---

## 1. Broken right now

1. **H — Alliance Advisor and Compare's alliance builder fail for every non-admin user.** Both call
   `POST /synergy/event/{k}/theoretical-alliance` (a read-only computation). The write middleware treats every
   POST outside a prefix allow-list as an admin write. Production returns 403 "Write access requires a valid
   X-Admin-Key header". Same trap: `POST /scouting/auto-drafts/generate|approve|reject` from the Scouting page.
   `backend/app/core/security.py:32`, `backend/app/api/routes_synergy.py:401`.
2. **H — Live scores and "LIVE" badges can be up to 5 minutes old.** `_cached_live_results_for_event` keeps a
   5 s Redis cache, but underneath `TBAClient._request` caches every GET in-process for 300 s, per uvicorn
   worker, so consecutive polls can even go backwards. Alliance picks, team-live-form and statuses share it.
   `backend/app/api/routes_matches.py:44`, `backend/app/tba/client.py:18`.
3. **H — Offline picklist edits can vanish silently.** A stale-version PUT returns HTTP 200
   `{ok:false, conflict:true}` (the header comment claims 409). The offline queue treats any 2xx as delivered
   and deletes it without its "dropped" event, contradicting "Nothing is ever dropped silently".
   `backend/app/api/routes_picklists.py:186`, `ScoutingApp/src/utils/offlineQueue.ts:300`.
4. **H — Room cleanup deletes planned assignments.** Rooms with no saved entries are deleted after 2 days of
   inactivity, assignments and leaders included, so a leader who plans assignments days ahead loses them.
   The cleanup also never clears `on_device_sessions.room_key` (plain FK, no ON DELETE); once such a room goes
   stale the job fails on it every run. `backend/app/services/scouting_rooms/maintenance.py:40`.
5. **H — Statbotics EPA silently drops out of ratings.** A module-global `httpx.AsyncClient`/`asyncio.Lock` is
   reused through `asyncio.run()` (new loop each call). Reproduced: the second call raises "Event loop is
   closed". In the RQ worker every recompute after the first gets `statbotics_error` for every team.
   Hidden today by Statbotics' own outage. `backend/app/services/clients/statbotics.py:198`,
   `backend/app/services/ratings/statbotics.py:44`.
6. **H — Anonymous GET creates fake events.** `GET /matches/event/{anything}/schedule` calls
   `_upsert_event_if_missing`, which inserts `Event(name=KEY.upper(), year=...)` even when TBA 404s. Typos
   become permanent events in local search/suggested lists. `backend/app/api/routes_matches.py:678`.
7. **H — Event-teams intel blocks the server's event loop.** `_build_event_teams_intel_payload` is `async` but
   runs synchronous SQLAlchemy, a synchronous TBA call (`requests` + `time.sleep` retries, 8 s timeout),
   the roster upsert and optional rating recomputes directly on the loop. Seven pages use it; on a cache miss
   every request and room WebSocket on that worker stalls. The team-intel sibling correctly uses an executor.
   `backend/app/api/routes_teams.py:1648`, `:1774`.
8. **H — Auto-scout can only produce empty drafts, and its Save path needs admin.** Every new video run
   carries `video_evidence.supported_form_fields = []`, so draft generation skips every field. Meanwhile the
   post-analysis hook, 30-min backfill, 24 h training export and predictor/ML stack keep running. In "auto"
   capture mode Save awaits a network approve (breaking local-first save) that 403s for non-admins.
   `backend/app/services/analysis/evidence.py:55`, `backend/app/services/auto_scout/scouting.py:436`,
   `ScoutingApp/src/pages/ScoutingPage.tsx:2558`.
9. **H — Last member leaving doesn't lock the workspace, despite saying it does.** The UI warns "Leaving locks
   this workspace's picklists, pit notes and rooms for good"; the code never sets `is_locked` or rotates the
   code, and the next person with the old code is auto-promoted to leader of all that data.
   `backend/app/api/routes_workspaces.py:260`.
10. **H — Offline on-device recording can't rely on its model offline.** The service worker only caches
    extensions matching `js|css|woff|…|json`; `.onnx` isn't one, so the 38 MB detector is never stored by the
    SW. `ScoutingApp/public/sw.js:19`.
11. **H (mine, PR #47) — tour prompt hidden behind the tab bar on tablets.** It lifts above the tab bar only
    below 900 px, but the app switches to the phone shell at 1120 px; at 1024 px (iPad) its buttons are covered.
    `ScoutingApp/src/components/tutorial/TourPrompt.css:35`.
12. **Fixed in the working tree today (uncommitted):** Home match cards overlapped their own scores at
    1180–1500 px (`productshell/shell-shared.css`).

## 2. Two systems for the same thing that disagree

1. **H — Three win-probability models for the same match.** Home/Match Center: backend sigmoid on raw rating
   margin (+ optional ML blend) `routes_events.py:288`; Match Prediction: ML shadow or its own synergy sigmoid
   `MatchPredictionPage.tsx:139`; Strategy Briefing: a second copy of that sigmoid `StrategyBriefingPage.tsx:42`.
   TBA's own predictions are proxied too (a fourth).
2. **H — Three official-data pipelines per team, not connected.** (a) ingest stores per-match official
   findings, `fuel_scoring_rate` = count ÷ **teleop** seconds × 60 (`scoring/breakdown.py:503`) → ratings;
   (b) `official_team_stats` fetches TBA COPRs live, `fuel_per_match_minute` = ÷ **whole-match** 160 s
   (`official_team_stats.py:230`) → Team Center; (c) `EventTeamStat` OPR/DPR → rating anchor and
   `/events/{k}/stats`. Both (a) and (b) are shown as "fuel/min" with different denominators (~14% apart).
3. **H — The quality gate is honoured by ratings and bypassed by Team Center.** All video findings are
   `ratings_eligible: False`. Ratings drop them (`ratings/data_loader.py:154`); Team Center, history and event
   histories use `rows_for_metric_coverage`, which falls back to the **rejected rows** when everything is
   rejected (`scouting_rooms/helpers.py:678`). Four overlapping gates exist (read-side, write-side,
   evidence, calibration).
4. **H — Overall scout rating computed twice, both on old-game scales.** Frontend
   `scoutingPage.helpers.ts:589` (logistic midpoints: auto 4.5, teleop 16, total 27 points) vs backend
   `routes_scouting_rooms.py:1320` (points/45, endgame/30, different weights). REBUILT robots score 50–350,
   so the points term is pinned at max in both; the server recomputes a different number than the scout saw.
5. **M-H — Match ingestion exists four ways.** `events/ingest.py` (`time` = scheduled), `routes_matches`
   TBA refresh (`time` = scheduled **or predicted**, per-row merge, `:735`), FIRST-API refresh, plus two
   roster-only upserts in `routes_teams` and `routes_scouting` (89 vs 102 lines, different rules). What
   `Match.time` means depends on who wrote last; nothing stores predicted/actual time.
6. **M-H — Two schedule endpoints.** `/matches/event/{k}/schedule` (FIRST-API-shaped params, live results)
   and `/events/{k}/schedule-with-synergy` (local rows, synergy, win prob) feed different pages.
7. **M-H — Push alerts run on the schedule, not reality.** Alerts and "live" detection use `Match.time`; events
   usually run 20–40 min behind, so "plays soon" fires early. Shift alerts also bind the scout's display name at
   subscribe time and break on rename. `push/match_alerts.py:58`.
8. **M-H — Two "my team" systems.** Workspace `frc_team_number` is read only by My Team; Strategy, Auto Paths,
   Data Viz and Scouting use a separate per-device `scouting_manual_my_team_v1`
   (`StrategyBriefingPage.tsx:28`). Favorites and push subscriptions are two more per-device team lists.
9. **M — Two alliance-selection systems.** Events shows official picks (TBA/FIRST); Picklist "Start alliance
   selection" makes scouts mark picks by hand and never reads the official picks.
10. **M — Two "data coverage" formulas.** Team Center (`routes_teams.py:374`: 0.42/0.30/0.16/0.12, ÷8) vs
    event team list (`:472`: 0.52/0.12/0.16/0.10/0.10, ÷6): the same team gets two scores.
11. **M — Rank fallbacks contradict each other.** Team intel refuses to rank a team against a field it never
    played ("a fabricated baseline"); event-teams intel does exactly that and writes the result into
    `tba.event_status_summary.rank`. Rating-derived EPA is presented under `statbotics` with `available: true`.
12. **M — Four team-key normalizers with different semantics** (`routes_matches`, pit regex, rooms
    lowercase-only with no `frc` prefix, intel). Rooms null out team/match/event keys that aren't in the DB.
13. **M — "Current season" has several sources.** Game config `CURRENT_SEASON_YEAR` (frozen at import) vs
    `_current_regional_automation_season` defined three times, falling back to the **calendar** year.
14. **M — Shift timing defined twice**: `season_template.json` `shift_schedule` and `season_config.py`
    constants, which call themselves the single source of truth. Recency window (20/10/2.0) is also defined
    twice: settings (ratings) and hard-coded constants (displayed stats).
15. **M — Provenance decided two ways**: `AnalysisRun.run_kind` vs string-sniffing `finding.source`; dedupe
    prefers video (priority 3) over official (1) though video is never ratings-eligible. Each screen uses a
    different match set: breakdown = video only, history = every kind, ratings = video + official.
16. **M — Two analysis enqueue systems** with separate dedupe keys (`analyze:` in `events/pipeline.py:74`,
    `live-analyze:` in `analysis/live_monitor.py:127`): one match can run twice concurrently (~20 min CPU each).
    Live monitor sessions live in process memory, so start/stop/status hit random workers.
17. **M — Two media-deletion systems** with different ages, gates and lock schemes (`media/retention` 2 days +
    disk budget vs scheduler `cleanup_old_media` 7 days; one deletes sampled frames, the other is told not to).
18. **M — Two capacity systems.** Ratings' `capacity_utilization` reads `TeamStaticCapability.ball_capacity`,
    which has no UI; pit scouting collects claimed capacity separately. Without it,
    `capacity = observed × 1.25`, so utilization is a constant 0.8.
19. **L-M — Two region labels** (`routes_events` "Texas" vs `team_utils` "TX"), three climb-success thresholds
    (0.5 / 0.8 / 0.35), two climb-level tables (38/68/100 with/without the 34 "unknown level").

## 3. Built, running, and going nowhere

1. **H — On-device sync (the stated primary direction) is invisible after sync.** Sessions stay
   `provisional` (`routes_tracks.py:945`); acceptance is an admin endpoint with no UI; ratings exclude
   `on_device`; Team Center is video-only; heatmaps need `include_on_device=true`, which the frontend never
   sends (`routes_tracks.py:88`).
2. **H — Elite analyzer + role classifier + ML role blend** (~700 lines) run for every team on every rating
   recompute (extra queries per team) and write `elite_dimensions` / `role_classification` that no screen
   reads. The role classifier is calibrated for another game (`/15` per minute, 6-second cycles) and lives in
   a file called `scouting_rooms/elite_detector.py`. `ratings/model.py:273`.
3. **H — Intel snapshot refresh (every 2 min) writes snapshots real users never read.** It builds with
   `auto_heal_ratings=True` (key `heal=1`); ordinary users request `heal=0`. Each run rebuilds up to 5 events
   and every team in them. When keys do match, "backfill missing fields only" keeps old values forever while
   extending expiry. `intel/snapshots.py:234`. Intel is also cached twice per request (Postgres + Redis).
4. **H — Model-QA subsystem is dead**: `POST /scouting/event/{k}/model-qa/refresh` and helpers, ~1,100 lines,
   zero callers anywhere; re-hardcodes the base rating weights. `routes_scouting.py:1049`.
5. **H — ~700 lines unreachable by call graph** (list in the appendix), including ~500 of 798 lines of
   `scoring/breakdown.py` (the whole cycle-fallback chain; `jobs.py` imports it and sets `{}`),
   `vision/track_analysis._detect_defensive_interactions` (why video defense is always empty),
   `events/classifier.load_transition_event_model` (the five `video_event_classifier_*` settings do nothing
   yet still change the analysis hash and re-queue jobs), `calibration/fuel_rate.estimate_fuel_rate_per_min`.
6. **M — 67 of 156 API routes have no frontend caller.** Many are legitimate admin/ops tools; notable:
   `/game-config/*` (7 routes; the frontend hard-codes season data instead), `/teams/{k}/capability`,
   on-device session review, Zebra MotionWorks (unavailable in 2026), several Statbotics/synergy/TBA proxies,
   `/tba/event/{k}` (a public proxy using the server key).
7. **M — Sparse fallback rating** (`routes_teams.py:612`): a second hand-built rating model in a route,
   centred on fuel 0.6 and auto 4.0, so every term saturates for REBUILT numbers.
8. **L — Dead config**: `core_metrics`, `event_labels`, `penalty_rules` in `season_template.json` (and they use
   different metric names than the DB); `fuel_scoring_rate_max_per_min` is a legacy-row heuristic, not a cap;
   module flags `EVENT_SEARCH_AUTOBACKFILL_ENABLED=False` and `SUGGESTED_EVENTS_REMOTE_TEAM_COUNT_FETCH_LIMIT=0`
   guard permanently-off code; legacy v1 token verifier; `RatingSnapshot` pruning is never called, so the
   append-only table grows forever.

## 4. Ratings correctness

1. **H — Tied values get arbitrary percentiles.** `ratings/helpers.py:177` ranks by list position
   (`{0,0,0,0,1}` → 0, 25, 50, 75, 100 — reproduced), and the team list comes from a query with no ORDER BY
   (`ratings/data_loader.py:93`), so the order is whatever Postgres returns. 42 percentile maps use it.
   About 96.5% of robots never climb, so most of a field ties at climb 0 and gets a climb percentile from 0 to
   ~96 by chance; climb is 20% of robot level. The same bug is copied into `ml/synergy.py:77`; a correct
   average-rank function already exists in `ml/model_eval.py` and isn't used.
2. **M — GET endpoints trigger whole-event recomputes.** `GET /scouting/event/{e}/team/{t}/rating` defaults
   `recompute_if_missing=True` with no admin check (`routes_scouting.py:1581`), unlike its siblings.

## 5. Performance and scaling design

- **M** All scouting-room endpoints are `async` but do synchronous DB work on the loop that serves the room
  WebSockets; team search (`teams/intel.py:46`) defaults to `include_event_registrations=true`, i.e. up to
  75 blocking TBA calls on the loop (the frontend passes false).
- **M** N+1 queries: event team histories (one query per team), elite/role per team per recompute, push alerts
  (queries per match per subscription each minute), sequential per-team Statbotics calls.
- **M** Caching layers stack so fast polling can't help: TBA 300 s in-process → Redis 5 s → HTTP 5 s →
  frontend memory 10 s (+30 s SWR) → 6 h localStorage; the frontend polls every 4–10 s.
- **L-M** Redis is cache + RQ queue + pub/sub with `maxmemory 2gb noeviction`: a full cache would block
  enqueues. Two uvicorn workers make every per-process cache and state diverge. Team intel opens up to 5
  extra DB sessions per request.

## 6. Data integrity, identity, and ops safety

- **M** Rooms key ownership, leaders, assignments and entries by the **display-name string**; names are
  unique only among active members, so a newcomer reusing a removed member's name inherits their assignments.
  Joins take no lock and there's no DB uniqueness, so concurrent joins can duplicate a name. The "profile
  already active — use a unique scout name" 409 now only fires for the same person on a second phone.
- **M** Picklist optimistic concurrency is read-compare-write without a lock (lost updates); pit scouting has no
  versioning at all and is replay-safe in the offline queue, so an old queued edit can overwrite a newer one.
- **M** Backups cover Postgres only: pit photos, ML shadow model artifacts (referenced by registry rows) and
  uploaded media live only on the VM disk.
- **M** A second, stale backup script (`backend/scripts/backup_postgres.sh:9`) passes the full `DATABASE_URL`
  as a `pg_dump` argument (the leak PR #53 fixed) and has no pooler guard (the #45 outage).
- **L** ~130 lines of Neon-era "missing table / privilege" detection in rooms, plus a
  `_bootstrap_missing_room_table` that always returns False yet is wired into 27 call sites
  (`routes_scouting_rooms.py:120`). Neon-quota messages in `main.py` and rooms are obsolete.

## 7. Bloat and duplication in numbers

- Backend: ~91 lines of byte-identical duplicate functions; same-name helpers with different contracts
  (`safe_float` ×6, `normalize_team_key` ×5, `clamp` ×5, `normalize_event_key` ×4, `mean` ×3,
  `canonical_pair` ×3, `event_year_from_key` ×3, `get_tba_client_or_503` ×3, `station_sort_key` ×3).
  `routes_teams.py` is a 2,054-line module with zero routes, kept so old `mock.patch` targets keep working;
  `services/team_breakdown.py` claims to decouple routes but imports the route handler. Three TBA client
  modules. `scouting_rooms/helpers.py` is the generic metrics library, misplaced under rooms.
- Frontend: 188 identical duplicated lines (Home and Events share ~15 copied date/calendar helpers;
  `teamFormStrip`, `inferMatchCompleted`, `matchHasScores`, `matchSortKey`, `readScoutProfile` ×3; `cx` ×7).
- CSS: 24,233 lines in 53 files; 274 selectors defined in two or more files; `.product-shell .surface-card`
  16 times across 4 files; 85 `!important`; a `shell-overrides.css` that exists to win cascade fights; two
  mobile breakpoints (1120 px shell vs 900 px SurfaceCard/TourPrompt/LiteStreamEmbed/Legal).
- Browser state: ~55 localStorage keys, 2 IndexedDB DBs, Cache Storage and a persistent GET cache; the same
  key string is redeclared under different constant names in up to 5 files.

## 8. Frontend UX and design

- **M** Home shows the empty state ("No matches scheduled…") for several seconds while schedules load.
- **M** The UI quality guards (`npm run guard:*`, 8 checks, ~5k lines of baselines) aren't in CI and haven't
  been updated since 2026-08-23.
- **L** `/#/primitives` (design-system demo) ships in production; Picklist lives under `/compare`.
- **L** The service worker header describes API caching it doesn't do; `CACHE_NAME` never changes, so old
  hashed assets accumulate; unhashed images are cache-first and never refresh.
- **L** Policy flip-flop: the 09-21 debloat deliberately removed idle route prefetch; PR #47 re-added it.

## 9. Infra, CI and docs drift

- **M** `AGENTS.md` (what Codex reads, last edited July 13) says prod is Neon + Upstash on two Container
  Instances and to use the Neon `-pooler` host (line 570). `docs/OCI_ENV_VARS.md` gives the same instructions
  under a one-line "moved" banner. `CLAUDE.md` contradicts itself (status: DB on the VM; conventions line 611:
  "In prod use the Neon -pooler host"; stale test counts).
- **L-M** CI tests on Postgres 16, prod runs 17; no `alembic check` for model/migration drift; ruff skips
  `scripts/`; the uptime workflow comment still says Neon/Upstash.
- **L** Config defaults describe a system that doesn't exist (pool 25+35, sampling 2 s/120 frames, ML blends
  0.0) while compose/.env set different values; the dev-token warning says "process-local ephemeral secret"
  but the fallback is deterministic.
- **L** Clutter: ignored `Scouting app/ubuntu@141cmob/` (an `scp` typo holding v1/v2 `.pt` weights), root
  `package.json` stub, week-0 one-off scripts, scripts that train the classifier runtime never loads.

## 10. Suggested order

1. Make "is this a write?" per-endpoint (fixes Alliance Advisor/Compare and auto drafts), and hide auto mode
   until video evidence supports fields.
2. Fix the percentile tie bug (average rank + ORDER BY) and recompute ratings; fix the Statbotics client
   per-loop; decide one "fuel/min" definition and one win-probability model.
3. Stop the TBA in-process cache from caching live data; move event-teams intel off the event loop.
4. Queue safety: treat `{ok:false, conflict}` as a conflict, add versions to pit scouting, keep rooms that have
   assignments, clear the on-device FK in cleanup, lock workspaces when the last member leaves.
5. Connect or retire: on-device sessions (show them or accept them), elite/role, model QA, snapshot refresh,
   the dead breakdown/classifier code, unused routes.
6. One source for season constants, current season, team keys, "my team", and match time (store predicted time).
7. Update `AGENTS.md`, `OCI_ENV_VARS.md` and `CLAUDE.md`; delete the old backup scripts; put the UI guards
   and `alembic check` in CI.

---

## Appendix — unreachable Python by call graph

`scoring/breakdown.py` 345 lines (`_rebuilt_expected_active_hub_duration_sec`, `_resolve_rebuilt_hub_activity_payload`,
`_fetch_match_payload_from_tba`, `_load_local_score_breakdown_cycle_fallbacks`, `_effective_fuel_rate_from_row`,
`_team_fuel_strength_weights`, `_rebalance_cycle_fallbacks_by_alliance`, `_resolve_sparse_cycle_fallbacks`; plus
`_score_breakdown_cycle_fallbacks_from_match_payload` and its helper, only imported), `vision/track_analysis.py`
93 (`_detect_defensive_interactions`), `events/classifier.py` 71 (`load_transition_event_model`,
`_resolve_model_path`), `calibration/fuel_rate.py` 48 (`estimate_fuel_rate_per_min`), `ratings/snapshots.py` 16
(`prune_rating_snapshots`), `vision/tracking_detection.py` 18 (`load_tracker_thresholds`),
`routes_scouting_rooms.py` 8 (`_require_scout_profile`), `season_config.py` 2 (`is_rebuilt_season`).
`auto_scout/on_device.py` and `on_device_cv.py` helpers are reference-only mirrors of the browser code, kept by
design for parity tests.
