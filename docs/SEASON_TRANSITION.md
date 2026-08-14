# Season transition guide (e.g. 2026 REBUILT → 2027)

The app is built to be **re-pointed** at a new season, not rewritten. Most game
rules live in data (`game_config/season_template.json`) and a handful of
season-keyed modules. This doc is the exact checklist of everything that changes
when the FRC game changes, in dependency order, with the failure modes that bite
if you skip a step.

**Golden rule:** if you find yourself adding a new `if year == 2027` branch in a
route or a UI component, stop — that value belongs in `game_config` or one of the
season-keyed modules listed below, not scattered in app code.

---

## Tier 0 — the data config (do this first, covers ~70%)

### `backend/app/game_config/season_template.json`
Single source for the field, zones, phases, scoring, and shift schedule. Update:
- `season_year` → new year.
- `game_name` / `SEASON_NAME`.
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
- `field_layout` / AprilTag anchors — for the (future) on-device pose pipeline.

Validation lives in `backend/app/game_config/schema.py`. If the new game needs a
new config shape, add the model + validator there. Reload is `lru_cache`d via
`load_game_config()` — tests call `reload_game_config()`.

### `ScoutingApp/src/config/gameFields.ts`
Frontend mirror of the game's scouting form fields.
- `SEASON_NAME`, the per-field labels, sections, and tap `step` increments
  (REBUILT scored fuel in bursts of 3 → `step: 3`).

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

### `backend/app/services/season_config.py`  ⚠️ needs a refactor pattern
Currently flat `REBUILT_*` constants (`REBUILT_TRANSITION_SHIFT_SEC`,
`REBUILT_ALLIANCE_SHIFT_SEC`, `REBUILT_ALLIANCE_SHIFT_COUNT`,
`REBUILT_ENDGAME_WINDOW_SEC`, `REBUILT_HUB_SCORE_GRACE_SEC`) plus
`shift_active_alliance()` / `rebuilt_active_hub_duration_sec()`.

If 2027 keeps an alternating-shift structure, prefer pulling these from
`game_config.shift_schedule` (the `shift_play.py` engine already does) rather than
adding `REBUILT2027_*` constants. If 2027 drops the mechanic, these helpers go
unused — leave them for historical 2026 reprocessing, don't delete.

---

## Tier 2 — 🔴 the silent-failure surface (read carefully)

### `backend/app/services/scoring/breakdown.py` + `truth.py`
Parses TBA score breakdowns into per-team truth. This is **heavily REBUILT-specific**
(`_extract_rebuilt_auto_fuel_count`, `_rebuilt_hub_activity_windows_by_alliance`,
`_infer_rebuilt_shift1_active_alliance`, …) and the season is gated with
`>= 2026` (e.g. `breakdown.py`, `routes_events.py:595`, `ratings/game_context.py:50`).

**The trap:** `>= 2026` means a 2027 event will *silently run REBUILT scoring math*
against a 2027 TBA breakdown whose JSON keys are different. It won't crash — it
will produce wrong/empty numbers that look plausible. TBA changes the score
breakdown schema every year.

**What to do for 2027:**
1. Change the gates from `>= 2026` to an explicit per-season dispatch (e.g.
   `if year == 2026: rebuilt_...` / `elif year == 2027: <new>` / else raise).
2. Write the 2027 breakdown parser against the real 2027 TBA payload — verify
   against actual JSON, not mocked happy paths (see CLAUDE.md: Events/knockouts
   gotcha).
3. Until the parser exists, **failing loud beats silently inheriting REBUILT.**

`ratings/constants.py` (penalty values) and `ratings/game_context.py` are in the
same family — REBUILT penalty/context values gated by year.

---

## Tier 3 — scattered year defaults (cosmetic but worth centralizing)

~12 files hardcode `2026` as a default fallback (`preferred_year: int = 2026`,
`Query(default=2026)`, `or 2026`, `Event.year.in_((2026, 2025))`):
`api/teams/{media,intel,stats}.py`, `api/routes_events.py`,
`api/routes_statbotics.py`, `services/ratings/data_loader.py`,
`services/ratings/game_context.py`.

These are mostly benign (real requests pass a year), but the "current + previous
season" fallback pairs like `(2026, 2025)` in `routes_events.py:1299/1311/1333`
**must** be bumped to `(2027, 2026)` or last-season fallback breaks.

**Recommended one-time refactor:** add a single `CURRENT_SEASON_YEAR` constant
(in `season_config.py` or `core/config.py`, ideally derived from
`load_game_config().season_year`) and replace the literals with it. Then a season
swap is one number. Until then, grep for `2026` before shipping a new season:
```bash
grep -rn --include="*.py" -E "\b2026\b" backend/app | grep -vE "test|txhou|demo|migration|20260"
```

---

## Tier 4 — models & ops (don't reset these)

- **ML detector model is season-specific.** `frc_robot_detector_v2.pt` was trained
  on FRC robots; a new game's robots/field may need retraining + a fresh locked
  holdout. See `docs/TRAINING.md`. The blend knobs (`ML_*_BLEND`,
  `ML_SHADOW_*`) and the deterministic fallback stay; only the weights change.
- **Calibration homographies are per-resolution and per-field** — old calibrations
  do not carry over. (See CLAUDE.md local-dev gotchas.)
- **Don't reset ratings logic** — ratings already zero out per season by design
  and fall back to last season with a disclaimer. Just make sure the year-pair
  fallbacks (Tier 3) point at the new pair.

---

## Quick checklist

- [ ] `season_template.json`: year, name, field, zones, phases, scoring,
      shift_schedule (or `null`)
- [ ] `schema.py`: new config shape if needed
- [ ] `gameFields.ts`: frontend form fields + `SEASON_NAME`
- [ ] `auto_scout/specs.py`: add `2027` to all `*_BY_SEASON` dicts +
      `mapper_version_for_season`
- [ ] `scoring/breakdown.py` + `truth.py`: **new TBA parser**, switch `>= 2026`
      gates to explicit per-season dispatch (verify vs real TBA JSON)
- [ ] `ratings/constants.py` + `game_context.py`: new penalty/context values
- [ ] year-pair fallbacks `(2026, 2025)` → `(2027, 2026)`
- [ ] grep for stray `\b2026\b` literals in `backend/app`
- [ ] retrain detector + lock a new holdout (`docs/TRAINING.md`)
- [ ] run `pytest` + `npm test`; verify a real event end-to-end before prod
