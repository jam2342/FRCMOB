# Rating roadmap: from rating_v15 to a full team model

Written 2026-10-14, the day rating_v15 shipped (PR #169). v15 rates a team by the points
it adds per match: teleop + auto + climb from official findings, shrunk toward earlier
events and the season's typical robot (`backend/app/services/ratings/README.md`). This
plan covers what comes next, in the order it should be built, and how each piece
proves itself before it ships.

**The rule for every step:** it ships only if `scripts/evaluate_rating_replay.py` shows
it beats what's live on held-out events, using only information known before each
match. The deterministic version stays as the fallback.

---

## Where v15 is weak (measured)

1. **Per-robot teleop is a guess at ingest, not an estimate.** TBA counts fuel per
   alliance. Ingest splits it with weights `clamp(1 + (0.08 × end-of-event OPR)^0.4,
   0.8, 4.5)` (`events/ingest.py compute_team_fuel_weight_priors`). That curve squashes
   teams toward an even split: a 100-OPR robot gets weight 3.3 beside a 20-OPR
   partner's 2.2. It also uses end-of-event OPR, which leaks later matches into
   earlier ones. Test on 8 events, predicting the last 30% of alliance teleop totals
   from the first 70%:

   | per-team estimate | alliance-total error (mean, pts) | spread of team values (SD) |
   |---|---|---|
   | today's split, averaged | 58.8 | 10–30 |
   | least squares on alliance totals | 60.4 (overfits) | — |
   | **ridge on alliance totals** (pulled toward the mean) | **54.8** | 19–43 |

   The split compresses real differences by roughly 35–45% and predicts worse, even
   with its leak.
2. **Early season and new events.** Matches where nobody has played here yet: 74.9%
   vs 78.6% once everyone has 3+ matches. Week 1 has no prior at all.
3. **One number, no range.** A team with 2 matches and a team with 18 look equally sure.
4. **Points only.** Defense, penalties, ranking points and alliance fit are invisible.
5. **Playoffs are less predictable** (Brier 0.163 vs 0.144 in quals) and nothing
   models why.

---

## Phase 0: groundwork — DONE 2026-10-15

### 0a. Alliance-level results: `match_alliance_results` (migration 0021)

One row per (match, alliance), written by `events/ingest.py` from the same TBA call as
the per-robot findings (`scoring/alliance_results.py`). Universal columns: team keys,
`surrogate_team_keys`, `dq_team_keys`, `total_score`, `score_without_fouls` (the
manual's 2nd ranking sort), `result` (win/loss/tie), `minor_fouls_committed`,
`major_fouls_committed`, `foul_points_received`, `ranking_points`. Season parts go in
`components` JSON so a new game needs no migration (2026: hub points for auto,
transition, shifts 1–4 and endgame; `hub_fuel_count`; auto/endgame tower points;
ENERGIZED / SUPERCHARGED / TRAVERSAL flags). `raw_breakdown` keeps TBA's payload for
re-parsing.

Checked against real TBA payloads before writing the parser: an alliance's
`minor/majorFoulCount` are fouls it **committed**, and its `foulPoints` are points it
**received** (120/120 Houston alliance-sides). ENERGIZED is hub `totalCount ≥ 100`;
TRAVERSAL is tower points ≥ 50.

Backfill: `scripts/backfill_alliance_results.py --season 2026` makes one TBA request per
event (no full re-ingest). On a production copy: 43,214 rows across 282 events in 93 s,
0 failures. Checks: stored alliance teleop equals the three robots' findings in 35,735 /
35,746 alliance-matches; season foul points received 226,525 vs 226,495 implied by the
other side's committed fouls; 102 ties; 195 surrogate and 450 DQ alliance-sides.

### 0b. Replay harness: `ratings/replay.py`

- A model implements `predict(card) -> Prediction(red_mean, blue_mean, red_var?, blue_var?)`,
  `observe(result)` and `team_points(team, event)`. `predict` gets a `MatchCard` with
  no scores at all, so a model *can't* see a result before forecasting it.
- `run_replay(season, model)` returns, on the newest 20% of events: accuracy, Brier and
  log loss overall and by slice (quals, playoffs, nobody has played here yet, everyone
  has 3+ here, a team at its first event), **alliance-points error** (prediction vs
  `score_without_fouls`), next-event Spearman/Pearson, and a calibration table. Win
  probabilities: a logistic on the margin (or on margin/σ when the model gives
  variances) fitted on the training events, per "matches seen here" bucket.
- Models so far: `StrengthV15` (the live rating) and `FuelOnly` (the old win
  probability). CLI: `scripts/evaluate_rating_replay.py --season 2026 [--models ...] [--grid]`.

### Baseline every later phase has to beat

Full 2026 season from a production copy: 21,588 matches, 281 events, 56 held out
(2,745 matches):

| model | accuracy | Brier | nobody played here yet | playoffs | alliance-points error | next-event ρ |
|---|---|---|---|---|---|---|
| **v15 (live)** | 78.1% | 0.148 | 0.161 | 0.159 | 73.0 pts | 0.780 |
| fuel only (old win prob) | 75.9% | 0.163 | 0.186 | 0.162 | 82.7 pts | 0.763 |

**v15 is overconfident at the extremes** (calibration table): predicted 3.3% → red won
7.7%; predicted 97.1% → red won 93.6%. Phase 1's probabilities from predicted
variances are the fix to look for. The v15 settings grid is flat (top five within
0.0001 Brier), so the live settings stay.

---

## Phase 1: match-by-match strength — DONE 2026-10-15 (rating_v16)

**What shipped.** `ratings/team_model.py`: per team and component (teleop hub, auto
hub) a belief (mean, variance); each alliance total updates its three robots by how
unsure we are about each (Kalman filter on a sum of three, full covariance within an
event, i.e. exact ridge on the event's alliance totals pulled toward each team's prior).
Variance grows +match_var per match and +event_var per new event. Tower points are per
robot (`components.robot_auto_tower_points` / `robot_endgame_tower_points`, added to the
parser), so they update one robot at a time. `ratings/team_model_service.py` replays the
season once (cached per process, keyed on the alliance results), and both
`ratings/model.py` and `events/win_probability.py` read it: played matches keep the
prediction made just before them, upcoming ones use `forecast()` (read-only on the
shared model). Win probability = logistic on margin / predicted spread, scaled per
"matches seen here" bucket.

Settings (chosen on the older 70% of training events, scored on the rest): teleop
prior var 4000, match 10, event 300, noise 4000; auto 400/2/150/400; tower 80/1/10/120.
Priors = season means per robot (teleop 50.9, auto 13.2, tower 0.7).

**The rating curve now has two anchors.** Under the team model, team-event points are
much wider (p10 9.5 → median 39.7 → p90 147 → p99 254 → max 431; v15: 21 → 49 → 127 →
204 → 340), because the old split had squeezed them. With "median = 50" alone the top
1% would read 98.8+. `strength.curve_for_anchors` fits
`100·(1 − e^(−(points/scale)^shape))` with median → 50 and p99 → 95 (scale 63.2, shape
0.789): p10 20, p25 33, p75 73, p90 86, best 98.9.

### Results (56 held-out events, 2,745 matches)

| model | accuracy | Brier | quals | playoffs | alliance-points error | next-event ρ |
|---|---|---|---|---|---|---|
| v15 as replayed (ingest's split) | 78.0% | 0.147 | 0.143 | 0.161 | 73.7 | 0.780 |
| v15, teleop **and auto** leak-free | 77.6% | 0.152 | 0.150 | 0.155 | 70.0 | — |
| **team model (v16)** | 78.0% | 0.152 | 0.154 | **0.147** | **65.4** | **0.791** |

Mid-season validation (older events): team model Brier **0.149** vs v15 0.1525,
alliance error 63.8 vs 77.2. Calibration at the extremes is only a little better
(predicted 3.7% → red won 6.1%; v15 3.2% → 7.6%; top bin 96.6% → 93.7%): still
overconfident, left for Phase 3.

**v15's replay numbers were partly a leak.** Ingest splits each alliance's auto points
with *end-of-event* auto COPR, so in a replay of a finished event v15's early-match auto
values already know the whole event. Re-fed with splits from earlier matches only, v15
falls from 0.147 to 0.1515 (teleop alone: 0.1476; auto is the leak). Live, during an
event, COPR is only as of that moment, so live v15 was mostly honest; but the 77.8% /
0.148 quoted when v15 shipped is optimistic. On a fair footing the team model ties v15
overall, is better mid-season and in playoffs, predicts points 11% better, and can't
leak by construction.

Tried and rejected on validation: larger event variance (worse at every size),
larger match variance (worse), an entry-variance floor (no gain on validation; held-out
quals unchanged), median priors (same as means). The team model's remaining weak spot is
late-season quals (DCMP, Championship): 0.154 vs leak-free v15 0.150.

---

## Phase 2: early season — DONE 2026-10-15

**Last season's strength as the starting belief.** `scripts/build_prior_strengths.py`
measures every team's end-of-season strength game-agnostically: the team model on each
alliance's whole score without fouls, z = ln(points / median team), clipped ±2.5,
stored in `team_prior_strengths`. 2025 came straight from TBA (one request per event,
104 s: 17,846 matches, 3,690 teams; top 2056, 2910, 1690, 1678, 1323), so the planned
7-hour re-ingest wasn't needed. A team's first 2026 belief = prior × exp(β·z).

**Rookies** (`teams.rookie_year`, from TBA at ingest and by `--rookies-only`) start at
prior × 0.6.

Chosen on the **first quarter of 2026 events** (where it matters), one setting at a time:

| | early-season Brier | mid-season (inner) | held-out (late) |
|---|---|---|---|
| none | 0.1623 | 0.1484 | 0.1507 |
| β 0.3 / 0.6 / **0.9** / 1.2 | 0.1558 / 0.1514 / **0.1492** / 0.1493 | 0.1479 / 0.1471 / 0.1461 / 0.1455 | ~0.1505 |
| **+ rookies × 0.6 (shipped)** | **0.1489** | **0.1460** | 0.1505 |
| + weekly prior curve | 0.1495 | 0.1474 | 0.1520 |

A narrower prior for known teams helped mid-season but hurt the early split, so it
stays off. The weekly curve (mean robot points by week) mixes the field improving with
which teams play which weeks; worse everywhere, not shipped. The late-season held-out
events don't move: by then every team has plenty of 2026 matches.

**For 2027:** run `build_prior_strengths.py --season 2026 --source db` at the end of
2026 and `--season 2027 --rookies-only` once teams register.

---

## Phase 3: uncertainty and reliability — DONE 2026-10-15

**Ranges.** Each rating shows its 80% range ("likely 96.7–97.8"): Team Center hero and
Rating Breakdown, Compare, Export CSV (`rating_range_80` in every rating API). The
spread is honest: 80% intervals on alliance totals hold 80.1% (validation) / 79.1%
(held-out) of results before duds, 81–82% after.

**Duds** (`team_model.py`): per alliance-match, the chance each robot scored ~nothing
(normal vs "robot i contributed nothing", weighted by each robot's dud rate); the rate
is that evidence shrunk toward the season base rate (10 matches' worth). Two uses ship:
predictions allow for duds (mean − p·μ, variance + p(1−p)μ²), and the update fills in
what a likely dud would have scored, so a breakdown doesn't read as the robot getting
worse. Base rate 2.5%, from EM on the training events. The validation splits disagreed
on it (inner kept improving to 8%, held-out preferred ~1%); 2.5% helps both.

| | inner Brier | held-out Brier | held-out quals | held-out playoffs |
|---|---|---|---|---|
| v16 | 0.1491 | 0.1519 | 0.1537 | 0.1466 |
| + upset floor | 0.1494 | 0.1526 | 0.1543 | 0.1474 |
| **+ duds (shipped)** | **0.1482** | **0.1507** | **0.1518** | 0.1475 |

**Not shipped:**
- *Upset floor* (`P = ε + (1−2ε)σ`): log loss better, Brier worse on both splits. The
  overconfidence isn't a uniform "anything can happen": predictions near 3% still win
  ~10% on validation.
- *Showing dud rates.* Held-out playoffs got slightly worse, and the rate is biased:
  only a strong robot's dud is visible in an alliance total (1678 8.6%, 254 1.9%; a
  weak robot's breakdown looks like a normal match). Stored in `details.dud_rate` for
  Phase 5, not shown.

Season run: 14.4 s cold (dud tracking adds ~2 s), still served stale-while-rebuilding.

---

## Phase 4: context — DONE 2026-10-15 (one term shipped)

Each term was added to the team model and scored on its own (validation picks, held-out
reported):

| term | inner Brier | held-out Brier | |
|---|---|---|---|
| base (v16 + duds) | 0.1482 | 0.1507 | |
| **playoff scale** | 0.1482 (playoffs 0.164 → 0.161) | 0.1506 (playoffs 0.1475 → 0.1459) | **shipped** |
| fouls (opponents' foul tendency → foul points received) | 0.1485 | 0.1515 | worse |
| defense (opponents' suppression of teleop) | 0.1481 | 0.1549 | worse |
| event offset (field's scoring level) | 0.1491 | 0.1515 | worse for winners, better totals (65.8 → 63.7) |
| diminishing returns (`a + b·s − c·s²` on totals) | 0.1482 | 0.1507 | the fit is ~identity on training |

The code for fouls, defense and the event offset stays in `team_model.py`, off by
default (`model_fouls`, `model_defense`, `event_offset_var`). TBA's convention
(verified in Phase 0): an alliance's foul counts are fouls it committed, its foul points
are what it received, so "foul points received = Σ opponents' tendencies".

What the diagnostic found and nothing fixed: on held-out events the highest predicted
alliances score 20–30 points under their prediction (lowest decile +6). Training events
don't show it, so it's late-season-specific (Championship-level defense?). It is why
ENERGIZED needed the calibration in Phase 5.

---

## Phase 5: ranking points and pick value — DONE 2026-10-15

**Bonus RP chances** (`ratings/ranking_points.py`): from the team model's predicted hub
fuel and tower points (mean, variance), the chance each alliance clears each bonus RP.
Thresholds are read per event from its own RP flags (Championship-level events announce
their own), with the manual's regional values as the fallback. Probabilities are
calibrated (σ(a + b·z), fitted on training events) where that helps:

| bonus RP | raw normal tail | calibrated (shipped) | base rate |
|---|---|---|---|
| ENERGIZED | 0.257 | **0.216** | 0.248 |
| SUPERCHARGED | 0.084 | **0.072** | 0.103 |
| TRAVERSAL | **0.0142** (shipped) | 0.0147 | 0.0165 |

The predictions API carries `bonus_rp_chances` and expected RP per alliance (quals); the
Predictions page has an "Exp. RP" column (bonus chances on hover).

**Pick value** (`ratings/pick_value.py`, `GET /events/{key}/pick-values?teams=`): for a
captain (+ first pick), each remaining team's chance to beat a strong alliance at the
event (its teams at the 95th/80th/65th percentiles) with them, through the team model and
the playoff scale; dud risk and uncertainty count automatically. Alliance Advisor shows
a "Best Next / Third Pick" card under the builder. (Assumes a typical third robot when
only the captain is chosen.)

---

## Phase 6: event simulation — DONE 2026-10-15

`simulation/event_sim.py` plays the rest of an event 2,000 times:
1. **Quals:** remaining matches with the calibrated win and bonus-RP chances; Ranking
   Score = RP / matches (surrogates excluded), ties by average alliance points.
2. **Selection:** top-8 captains, serpentine, best available; a picked captain's seat
   empties and the next ranked team is promoted (manual 10.6.1).
3. **Playoffs:** the 8-alliance double-elimination bracket and a best-of-3 final, from
   the team model's alliance strengths and the playoff scale.

`GET /events/{key}/forecast` (cached until the event's results change); Events page
**Forecast** tab: expected rank, P(top 8), P(captain), P(playoffs), P(win).

Replayed at 25/50/75% of quals on 29 held-out events (881 team-events), scored against
how they actually ended:

| point in quals | P(top 8) Brier: forecast | "current rank holds" | P(win event): forecast | even-chance baseline |
|---|---|---|---|---|
| 25% | **0.086** | 0.211 | 0.088 | 0.0885 |
| 50% | **0.059** | 0.127 | **0.081** | 0.0885 |
| 75% | **0.040** | 0.084 | **0.081** | 0.0885 |

Top 8 is called well; the event winner only modestly better than even chances (picks
on real teams follow scouting, not just strength, and playoffs are short).

---

## Phase 7: learned correction — DONE 2026-10-15 (shipped)

`ratings/win_correction.py`: a 14-input, 16-unit network on features known before each
match (margin/σ, the team model's logit, best and worst robot on each side, dud rates,
uncertainty, playoff, matches here / at earlier events, week of season, tower gap, total
level). Features come from the season replay production runs, so nothing leaks.
`scripts/train_win_correction.py --season 2026` reproduces the weights
(`app/services/ratings/data/win_correction_2026.json`, 5.9 KB).

| | team model | logistic correction | **network (shipped)** |
|---|---|---|---|
| validation Brier | 0.1449 | 0.1450 | **0.1412** |
| held-out Brier | 0.1503 | 0.1477 | **0.1464** |

Five seeds agree (0.1412–0.1413 / 0.1462–0.1464). It also fixes the overconfidence the
earlier phases couldn't: held-out, predicted 4.6% → red won 4.7% (team model alone: 3.9%
→ 5.8%). `CORRECTION_BLEND` (1.0) blends it with the deterministic probability, which
stays in the API (`red_win_prob_deterministic`); label `team_model_corrected_v4`. A
season without weights gets none (retrain once a new season has events).

---

## Data hygiene (do alongside Phase 0/1)

- **60 events have only old backfill rows** (teleop split evenly, auto unknown).
  Phase 0 stored their real alliance totals from TBA, so Phase 1 (which reads alliance
  totals) doesn't depend on them. v15 still does: find out why the 2026-10-05 full
  re-ingest didn't replace them before re-ingesting.
- **Surrogates, DQs, no-shows** (Phase 0 table): a DQ'd alliance scores 0 in playoffs.
  Exclude DQ matches from strength. Keep surrogate matches for strength (the play is
  real) but out of the Ranking Score in simulation.
- **Replays**: TBA keeps the replayed match. Make sure a replayed match isn't counted
  twice.
- **"Why did my rating change" — done:** the season run records each team's expected points before and after every match; ratings keep the last five (`details.recent_changes`) and Team Center lists them under Rating Breakdown ("QM 34: +1.2 rating · +8 pts/match").

---

## Order, effort, and what each unlocks

| Phase | Effort | Unlocks | Gate |
|---|---|---|---|
| 0 groundwork | **done 2026-10-15** | every later phase | — |
| 1 match-by-match strength | **done 2026-10-15** | honest per-robot points, variance, better win prob | alliance error, Brier, next-event |
| 3 ranges + dud risk | **done 2026-10-15** | ranges in UI, risk-adjusted picks | calibration |
| 2 early season | **done 2026-10-15** | week-1 predictions | first-event slice |
| 4 context terms | **done 2026-10-15** (playoff scale) | defense, penalties, event/playoff effects | per-term |
| 5 RP + pick value | **done 2026-10-15** | Picklist/Advisor sort by real value | RP Brier |
| 6 event simulation | **done 2026-10-15** | forecasts for fans | rank/captain Brier |
| 7 learned corrections | **done 2026-10-15** | small accuracy gains | Brier ≥ 0.003, calibration |

Phase 3 comes before 2 because ranges fall straight out of Phase 1, and Phase 2 waits
on a 7-hour 2025 ingest that can run in the background meanwhile.

## Keep it portable to 2027

Everything above works on "components reported per alliance" (+ "components reported
per robot", like climbs). For a new game, `official_points.py` and the Phase 0 table
map the new breakdown to components, and the rest is unchanged. The cross-season prior
(2a) is what makes week 1 of 2027 better than week 1 of 2026.

## What not to do

- Don't fit anything on end-of-event inputs (OPR, COPR, end-of-event ratings) and call
  it a prediction. That's the 2026 ML leak.
- Don't put the event offset or alliance partners into the team's rating. The rating
  is about the robot, and context belongs in predictions.
- Don't show dud risk, defense or penalties per team until the harness shows they
  predict something. A confident wrong number is worse than a blank.
