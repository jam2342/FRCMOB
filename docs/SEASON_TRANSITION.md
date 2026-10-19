# Season transition guide (e.g. 2026 REBUILT → 2027)

The app is built to be **re-pointed** at a new season, not rewritten. Most game
rules live in data (`game_config/season_template.json`) and a handful of
season-keyed modules. This doc is the exact checklist of everything that changes
when the FRC game changes, in dependency order, with the failure modes that bite
if you skip a step.

**Start here:** `cd backend && PYTHONPATH=. python scripts/season_readiness.py --season 2027 --db`
prints every item below that is still missing for the season (and exits 1 while anything
needed before the first events is missing). Re-run it as you go; it reads the same season
tables the app does, so it can't drift from the code.

**Golden rule:** if you find yourself adding a new `if year == 2027` branch in a
route or a UI component, stop — that value belongs in `game_config` or one of the
season-keyed modules listed below, not scattered in app code.

---

## Tier 0 — the data config (do this first, covers ~70%)

### `backend/app/game_config/season_template.json`
Single source for the field, zones, phases, scoring, and shift schedule. Update:
- `season_year`, `season_key`, `season_name` → the new game.
- `field.length_m` / `field.width_m` — **only if the field perimeter changes** (it
  was constant 2024–2026). Everything downstream (heatmaps in `routes_tracks.py`,
  `shift_play.py`) now reads these from config, so changing them here is enough.
- `zones` — the named field regions for the new game.
- `phases` — auto/teleop/endgame timing (must sum to `total_sec`).
- `scoring` — game-piece point values.
- `shift_schedule` — **only if the new game has an alternating-hub mechanic like
  REBUILT.** If it doesn't, set this to `null`; `shift_play.py` already raises a
  clean "shift_schedule is not configured for this season" and the Attack/Defense
  card simply won't render. `role_zones` keys must reference real `zones` keys
  (validated in `schema.py`).

Validation lives in `backend/app/game_config/schema.py`. If the new game needs a
new config shape, add the model + validator there. Reload is `lru_cache`d via
`load_game_config()` — tests call `reload_game_config()`.

### `ScoutingApp/src/config/season.ts`
The frontend's only copy of season facts: year, fallback year, game name, and
match/auto/endgame length. The scouting timer, Match Center progress bar, and
event-search year defaults all read it. It is a static snapshot (scouting runs
offline), and `season.test.ts` reads `season_template.json` and fails the build
if the two disagree — so bump both together. (Before this existed the timer
assumed a 150 s match while REBUILT is 160 s.)

### `ScoutingApp/src/config/gameFields.ts`
Frontend mirror of the game's scouting form fields: per-field labels, sections,
and tap `step` increments (REBUILT scored fuel in bursts of 3 → `step: 3`).
The form state types (`scoutingPage.types.ts`) still name REBUILT fields
(bump/trench/climb), so a game with different actions needs those types too.

---

## Tier 1 — season-keyed code modules (extend the dict, don't branch)

These already follow the right pattern: a `{year: ...}` mapping. **Add a 2027
entry; do not touch the 2026 entry.**

### `backend/app/services/auto_scout/specs.py`
- `AUTO_SCOUT_V1_FORM_FIELDS_BY_SEASON` → add `2027: (...)`.
- `AUTO_SCOUT_DERIVED_INSIGHT_FIELDS_BY_SEASON` → add `2027: {...}`.
- `AUTO_SCOUT_MIN_TRACK_COVERAGE_BY_SEASON` → add `2027: <float>`.
- `mapper_version_for_season()` → add the `2027` case (drafts are keyed by
  `mapper_version`, so a new season must get a new version string or old drafts
  collide).

### `backend/app/services/season_config.py`
Currently flat `REBUILT_*` constants (`REBUILT_TRANSITION_SHIFT_SEC`,
`REBUILT_ALLIANCE_SHIFT_SEC`, `REBUILT_ALLIANCE_SHIFT_COUNT`,
`REBUILT_ENDGAME_WINDOW_SEC`, `REBUILT_HUB_SCORE_GRACE_SEC`) plus
`shift_active_alliance()` / `rebuilt_active_hub_duration_sec()`.

`require_known_season_rules()` is now the central fail-closed guard: future
seasons are rejected until their rules are deliberately implemented. Keep the
guard in every season-specific scoring, truth, ratings, and phase entrypoint. It
compares against `REBUILT_SEASON_YEAR` today, so accepting 2027 means changing it to
the set of seasons that have a rules adapter, not just bumping the number.

If 2027 keeps an alternating-shift structure, prefer pulling these from
`game_config.shift_schedule` (the `shift_play.py` engine already does) rather than
adding `REBUILT2027_*` constants. If 2027 drops the mechanic, these helpers go
unused — leave them for historical 2026 reprocessing, don't delete.

---

## Tier 2 — 🔴 the silent-failure surface (read carefully)

### `backend/app/services/scoring/breakdown.py` + `truth.py`
Parses TBA score breakdowns into per-team truth. This is **heavily REBUILT-specific**
(`_extract_rebuilt_auto_fuel_count`, `_rebuilt_hub_activity_windows_by_alliance`,
`_infer_rebuilt_shift1_active_alliance`, …). The old broad `>= 2026` gates have
been replaced with exact-2026 dispatch plus `require_known_season_rules()`.

**The trap:** `>= 2026` means a 2027 event will *silently run REBUILT scoring math*
against a 2027 TBA breakdown whose JSON keys are different. It won't crash — it
will produce wrong/empty numbers that look plausible. TBA changes the score
breakdown schema every year.

**What to do for 2027:**
1. Extend the existing explicit per-season dispatch (`2026 → REBUILT`, `2027 →
   new adapter`, otherwise raise).
2. Write the 2027 breakdown parser against the real 2027 TBA payload — verify
   against actual JSON, not mocked happy paths (see CLAUDE.md: Events/knockouts
   gotcha).
3. Until the parser exists, **failing loud beats silently inheriting REBUILT.**

The rating (`services/ratings/`, rating_v15) is in the same family. It reads only
official per-robot points: `ratings/official_points.py` turns one official finding
into teleop / auto / climb points for REBUILT, and `ratings/constants.py`
`SEASON_BASELINES` holds the season's typical robot (what a rating of 50 means).
A season with no baseline row raises, so ratings fail closed until both exist.

---

## Tier 3 — year defaults (done: one number now)

Year defaults and the "current + previous season" pairs read `CURRENT_SEASON_YEAR` /
`PREVIOUS_SEASON_YEAR` from `season_config.py`, which comes from
`season_template.json`'s `season_year`, so they move with the game config. The `2026`
literals left in `backend/app` are keys of season tables (`*_BY_SEASON`, `SEASON_BASELINES`,
`COMPONENT_PARSERS`, `BONUS_RPS`, `COPR_METRICS_BY_SEASON`, `SEASON_GAMES`) — add a 2027
key beside them — plus `scouting_rooms/helpers.py`, which masks legacy 2026 ingest values
on purpose and stays 2026-only. Check for new stray ones before shipping a season:
```bash
grep -rn --include="*.py" -E "\b2026\b" backend/app | grep -vE "test|txhou|demo|migration|20260"
```

---

## Tier 4 — models & ops (don't reset these)

- **ML detector model is season-specific.** `frc_robot_detector_v2.pt` was trained
  on FRC robots; a new game's robots/field may need retraining + a fresh locked
  holdout. See `docs/TRAINING.md`.
- **Calibration homographies are per-resolution and per-field** — old calibrations
  do not carry over. (See CLAUDE.md local-dev gotchas.)
- **Ratings start over each season by design**: a team's strength only reads
  matches from the same season. Early in a new season everything leans on the
  baseline's typical robot, so set a provisional baseline from week 1 and
  refresh it once a few weeks of events are in (`scripts/evaluate_rating_replay.py
  --season 2027` prints the medians, and replays the season to check the rating
  still predicts matches).
- **Old seasons stay readable, frozen** (`services/seasons.py`). After the last refresh
  of 2026, run `scripts/freeze_season.py --season 2026` on the VM: it archives every
  2026 match prediction and marks 2026 read-only, so its ratings are never recomputed
  with 2027 code and its pages read the archive instead of rebuilding the season in
  memory (~20 s and a second season's worth of RAM per worker otherwise). Pages for a
  past event show a "2026 season · REBUILT · final ratings" strip; Team Center shows a
  team with no 2027 rating as "Not rated yet" with last season's on its own line, and
  Event history lists ratings season by season. Seasons before `FIRST_RATED_SEASON`
  (2022–2025) have results but no ratings, and say so.

---

## Kickoff timeline (2027)

The game is revealed at kickoff (early January); week 0 events start late February.
Everything game-specific waits for the manual and the first real TBA payloads; the rest
can't break anything meanwhile: ingesting a 2027 event before its rules exist fails
closed (`require_known_season_rules`), so the live-ingest job (every 3 min, events in
play) and `scripts/live_consistency_check.py` (cron on the VM) only log errors for
2027 events until then, and 2026 pages keep working.

| When | What | Time |
|---|---|---|
| End of the 2026 off-season (Dec) | `build_prior_strengths.py --season 2026 --source db` (early-season prior) | ~1 min |
| Kickoff day | Read the manual: scoring, bonus RPs, match timing, field → Tier 0 config (`season_template.json`, `season.ts`, `gameFields.ts`) | ~half a day |
| Kickoff week | Scouting form fields; game name in `SEASON_GAMES`; provisional `SEASON_BASELINES` row (typical points from the manual's scoring and 2026's spread) | 1–2 days |
| First TBA payloads (week 0) | Breakdown parser + `COMPONENT_PARSERS` + `COPR_METRICS_BY_SEASON`, verified against real JSON; `require_known_season_rules` allows 2027 | 1–2 days |
| Rules landed, deployed | Bump `AUTOMATION_REGIONAL_HALFDAY_SEASON` on the VM, recreate containers; `freeze_season.py --season 2026`; `season_readiness.py --season 2027 --db` ready | ~1 h |
| After week 1 | Real `SEASON_BASELINES` from `evaluate_rating_replay.py --season 2027`; bonus RP calibration | ~2 h |
| After week 2–3 | `train_win_correction.py --season 2027`, ship only if it beats the team model | ~1 h |

Deploy at quiet times: a backend restart costs ~30 s of slow prediction pages while
each worker rebuilds the season (measured 2026-10-18).

## Quick checklist

- [ ] `season_template.json`: year, name, field, zones, phases, scoring,
      shift_schedule (or `null`)
- [ ] `schema.py`: new config shape if needed
- [ ] `season.ts`: year, name, match timing (`npm test` fails until it matches)
- [ ] `gameFields.ts` + `scoutingPage.types.ts`: frontend form fields
- [ ] `auto_scout/specs.py`: add `2027` to all `*_BY_SEASON` dicts +
      `mapper_version_for_season`
- [ ] `scoring/breakdown.py` + `truth.py`: **new TBA parser**, then add an
      explicit 2027 dispatch branch (verify vs real TBA JSON)
- [ ] `ratings/official_points.py`: per-robot points from the new breakdown
- [ ] `scoring/official_team_stats.py` `COPR_METRICS_BY_SEASON`: TBA's component-OPR
      names for the new game (Team Center's official fuel/auto numbers)
- [ ] `scoring/alliance_results.py`: a `COMPONENT_PARSERS` entry for the new
      breakdown (alliance totals per component, per-robot climbs), then
      `scripts/backfill_alliance_results.py --season 2027`
- [ ] `ratings/team_model.py` `HUB_COMPONENTS`: the alliance-total components to model
- [ ] last season's strengths as the early-season prior: `scripts/build_prior_strengths.py
      --season 2026 --source db` (end of 2026), then `--season 2027 --rookies-only`
- [ ] `ratings/ranking_points.py`: the new game's bonus RPs in `BONUS_RPS` (which part
      counts, the manual's threshold, the components key) and, once events are in,
      `BONUS_RP_CALIBRATION` from `evaluate_rating_replay.py` ("bonus_rp")
- [ ] the learned win correction: none until the season has events (2026's weights don't
      apply); then `scripts/train_win_correction.py --season 2027`, and re-check that the
      report beats the team model before shipping the file
- [ ] `ratings/constants.py`: a `SEASON_BASELINES` row (team-model priors and
      settings, median and p99 robot points for the rating curve, typical
      components, max climb points) and `win_probability.py` scales; all printed by
      `scripts/evaluate_rating_replay.py --season 2027 --models team_model --grid-team`
- [ ] grep for stray `\b2026\b` literals in `backend/app`
- [ ] retrain detector + lock a new holdout (`docs/TRAINING.md`)
- [ ] production `.env` on the VM: `AUTOMATION_REGIONAL_HALFDAY_SEASON=2026` pins the
      scheduler's regional automation to REBUILT on purpose; bump it only after the
      2027 rules above land (`0` = current UTC year), then recreate the containers
- [ ] the new game's name in `SEASON_GAMES` (`services/seasons.py` and `season.ts`)
- [ ] freeze the old season once its last refresh is done: `scripts/freeze_season.py
      --season 2026` (refuses the current season, so after the game config moves to 2027)
- [ ] `scripts/season_readiness.py --season 2027 --db` reports ready
- [ ] run `pytest` + `npm test`; verify a real event end-to-end before prod
