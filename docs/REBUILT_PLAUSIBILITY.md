# 2026 REBUILT scoring plausibility

> Research from 2026-09-27 (TBA 2026 score breakdowns and COPRs, Statbotics, Chief Delphi, the game manual).
> Acted on: Team Center no longer derives fuel, auto, cycle, climb, defense or reliability from the rating.
> Fuel and auto come from TBA component OPRs, climb from the official per-robot record, and the rest stay
> empty until scouted (`backend/app/services/scoring/official_team_stats.py`). The "current estimator" sections
> below describe the code as it was before that change.

> Ingest follow-up: 2026 TBA alliance breakdowns now leave `cycle_time_sec` empty and
> apportion auto hub fuel only when all three robots have event auto-fuel COPRs.
> Per-robot auto remains unknown when those estimates are unavailable; observed
> auto tower points stay separate in the status data. New imports use
> `tba_score_breakdown_v2`, and ratings ignore the old v1 cycle and equal-auto
> values. The production refresh and ratings recompute completed on 2026-09-27:
> 268 events with stored matches were refreshed, leaving 104,544 v2 official
> findings, no v1 findings, and no official `cycle_time_sec` values. Of the v2
> findings, 315 have unknown per-robot auto because their alliances lacked
> complete auto-fuel COPRs. The bounded, dry-run-first
> `backend/scripts/refresh_2026_official_truth.py` skips events with no stored
> matches and guards against TBA omitting a match that already has official
> findings; incomplete COPRs leave auto unknown without blocking the refresh.
> Zero-output robots still receive an official finding, so official match
> counts include their appearances.
> Run it from `backend/` as `python -m scripts.refresh_2026_official_truth`;
> direct execution of the file does not put `app` on Python's import path.

## Bottom line

- The official-season sample is 213 events, 18,372 matches, 36,744 scored-alliance observations, and 110,232 robot appearances. Preseason and offseason events are excluded. This reproduces the 18,372-match count on [TBA's 2026 insights page](https://www.thebluealliance.com/insights/2026).
- REBUILT changed drastically over the season. An early-season qualification alliance had median 99 fuel; a Championship qualification alliance had median 404; a Championship playoff alliance had median 577.5. A single season-wide threshold will either reject legitimate Championship performance or accept wildly implausible early-event inputs.
- At robot level, Statbotics' full-season distribution is the cleanest prior: total-fuel EPA is 36.52 at p50, 125.50 at p90, 230.63 at p99, and 358.51 maximum. Converted to fuel per 160-second match-minute, those are 13.69, 47.06, 86.49, and 134.44 fuel/min.
- TBA COPR is useful for estimating an individual robot from alliance-only data, but it is not a physical measurement. Of 8,160 official event-team observations, 501 total-fuel COPRs are negative and the event-team maximum is 472.14. Do not use raw COPR extrema as hard physical limits.
- There is no rules limit on robot fuel possession. There are 504 staged fuel in a normal match (approximately ±24), up to 600 at District/World Championship, but scored fuel returns to the field. Therefore neither fuel per robot nor total fuel scored has a finite rules ceiling. The observed alliance maximum is 964 fuel.
- Recommended app behavior: use event/week-conditioned warning bands; reject only dimensionally or rules-impossible values; show a warning rather than hard rejection above empirical p99; and use Statbotics EPA or TBA component OPR directly for zero-scouting estimates.

For Team 254 specifically, the app's 144 fuel/min is semantically mixed and high, 34 s cycle time is incompatible with that rate, 57 auto points is materially low, and 0% climb agrees with the raw 2026 results but is overconfident as a probability. Details are in [Team 254 and the current estimator](#team-254-and-the-current-estimator).

## Definitions and method

The source JSON contains 268 events and 20,998 matches. I retained official event types 0–5 and removed 1 preseason and 54 offseason events, leaving 213 events and 18,372 matches.

- **Early season:** Regional or District event, week 0–2.
- **Championship:** Championship Division or Championship Finals, event type 3 or 4. District Championships are part of the season-wide official sample, not the `Championship` cohort.
- **Qualification:** `comp_level == "qm"`; all other official matches in this dataset (`sf`, `f`) are playoffs.
- **Percentiles:** linear interpolation at `(n-1)q`.
- **Table notation:** every distribution cell is `mean | p10/p50/p90/p99/max`.
- **Fuel per active alliance shift:** `(shift1 + shift2 + shift3 + shift4) / 2`. Each alliance has exactly two active 25-second alliance shifts. This measure excludes transition and endgame.
- **Robot match-minute:** total fuel divided by the full 160-second match, multiplied by 60.
- **Robot active-hub second:** total fuel divided by 110 active seconds: 20 auto + 10 transition + two 25-second alliance shifts + 30 endgame. The teleop-only active denominator is 90 seconds.

The [official game manual](https://firstfrc.blob.core.windows.net/frc2026/Manual/HTML/2026GameManual.htm) is authoritative for timing, hub status, staged fuel, unlimited robot control, and tower values. Statbotics values came from its [public 2026 team-year site datastore](https://storage.googleapis.com/site_v1/team_years/2026), last modified 2026-06-12, containing 3,724 teams. The documented [Statbotics API](https://statbotics.readthedocs.io/en/latest/) returned HTTP 500 during this analysis, so the report uses the same current-year datastore consumed by the Statbotics site rather than pretending the REST call succeeded.

The complete calculations are reproducible with `python3 analyze_rebuilt.py`; machine-readable results are in `rebuilt_analysis_results.json`.

## 1. Alliance performance

### Fuel

| Cohort | Alliance observations | Auto fuel | Teleop fuel | Total fuel | Fuel per active 25 s shift |
|---|---:|---:|---:|---:|---:|
| Early qualification | 13,388 | 25.83 \| 9/21/50/87/161 | 95.87 \| 19/77/201/336.13/597 | 121.71 \| 32/99/245.30/408.13/758 | 30.86 \| 4.5/24.5/66/111/189 |
| Early playoff | 3,010 | 41.36 \| 17/35/75/118/165 | 148.08 \| 47/134/274.10/427/564 | 189.45 \| 69/171/342/530.55/716 | 47.46 \| 13.5/42/89/137.5/183 |
| Championship qualification | 1,994 | 81.89 \| 37/80/127/165/203 | 325.08 \| 161.30/320/484/626/752 | 406.97 \| 219/404/591/774.07/903 | 99.99 \| 50.5/98.5/151.7/195.64/242.5 |
| Championship playoff | 276 | 128.47 \| 80.5/129.5/171.5/201.75/224 | 447.04 \| 297.5/454/597.5/667.25/814 | 575.51 \| 401.5/577.5/745.5/844.25/964 | 133.76 \| 82.75/135.5/178.25/211.12/221 |
| All official qualification | 30,352 | 35.91 \| 11/27/74/128/213 | 140.30 \| 25/112/299/494.49/752 | 176.21 \| 40/141/367/599/903 | 44.27 \| 6.5/35.5/95.5/155.5/242.5 |
| All official playoff | 6,392 | 53.66 \| 19/44/103/166/224 | 195.72 \| 59/169/373/574.09/826 | 249.38 \| 84/215/468/712.09/964 | 60.81 \| 17.5/53.5/116.45/175.05/255 |

The Championship playoff average total fuel is 575.51, close to the 577.5 median; the 964 maximum is Daly Finals 1 and matches [TBA's listed highest score](https://www.thebluealliance.com/insights/2026). It consisted of 150 auto fuel and 814 teleop fuel with no tower or foul points.

### Points, tower, and fouls

`foul_points` are points credited to the alliance because of opponent fouls, not fouls committed by that alliance.

| Cohort | Alliance observations | Auto points | Tower points | Foul points credited | Total points |
|---|---:|---:|---:|---:|---:|
| Early qualification | 13,388 | 26.50 \| 9/21/51/89/161 | 2.08 \| 0/0/10/30/60 | 4.10 \| 0/0/15/40/425 | 127.89 \| 36/106/253.30/416.13/768 |
| Early playoff | 3,010 | 42.46 \| 17.9/37/76.1/119/165 | 2.63 \| 0/0/10/30/50 | 7.82 \| 0/5/20/60/115 | 198.90 \| 75/179/354/544.55/725 |
| Championship qualification | 1,994 | 82.79 \| 38/81/128.7/165/203 | 1.51 \| 0/0/0/25/55 | 7.91 \| 0/5/20/60/140 | 416.40 \| 227.3/414/599/779.07/908 |
| Championship playoff | 276 | 128.63 \| 80.5/129.5/172.5/201.75/224 | 0.60 \| 0/0/0/18.75/45 | 10.14 \| 0/5/25/61.25/75 | 583.97 \| 405.5/585/756.5/845.75/964 |
| All official qualification | 30,352 | 36.61 \| 11/28/75/129/213 | 1.94 \| 0/0/10/30/80 | 4.54 \| 0/0/15/45/425 | 182.69 \| 44/147/374/608/908 |
| All official playoff | 6,392 | 54.56 \| 20/45/104/166.09/224 | 2.11 \| 0/0/10/30/50 | 7.71 \| 0/5/20/55/125 | 257.58 \| 90/225/476/719.09/964 |

Foul counts are extremely zero-inflated. In official qualification matches, minor-foul count is `0.39 | 0/0/1/3/11` and major-foul count is `0.17 | 0/0/1/3/28`. In official playoffs those become `0.68 | 0/0/2/4/9` and `0.29 | 0/0/1/3/8`. The 425-point foul maximum is genuine score-breakdown data, but it is an extreme disciplinary outcome and should not calibrate robot scoring ability.

## 2. Per-robot performance

### Equal-split lower-bound sanity check

Dividing an alliance by three is not a robot estimator: it understates the primary scorer and overstates a non-scorer. It is still a useful consistency check for an app value that claims to be an ordinary robot contribution.

| Cohort | Auto fuel/robot | Teleop fuel/robot | Total fuel/robot | Fuel/match-minute | Fuel/active-hub second | Auto points/robot |
|---|---:|---:|---:|---:|---:|---:|
| Early qualification | 8.61 \| 3/7/16.67/29/53.67 | 31.96 \| 6.33/25.67/67/112.04/199 | 40.57 \| 10.67/33/81.77/136.04/252.67 | 15.21 \| 4/12.38/30.66/51.02/94.75 | 0.369 \| 0.097/0.300/0.743/1.237/2.297 | 8.83 \| 3/7/17/29.67/53.67 |
| Early playoff | 13.79 \| 5.67/11.67/25/39.33/55 | 49.36 \| 15.67/44.67/91.37/142.33/188 | 63.15 \| 23/57/114/176.85/238.67 | 23.68 \| 8.62/21.38/42.75/66.32/89.5 | 0.574 \| 0.209/0.518/1.036/1.608/2.170 | 14.15 \| 5.97/12.33/25.37/39.67/55 |
| Championship qualification | 27.30 \| 12.33/26.67/42.33/55/67.67 | 108.36 \| 53.77/106.67/161.33/208.67/250.67 | 135.66 \| 73/134.67/197/258.02/301 | 50.87 \| 27.38/50.5/73.88/96.76/112.88 | 1.233 \| 0.664/1.224/1.791/2.346/2.736 | 27.60 \| 12.67/27/42.9/55/67.67 |
| Championship playoff | 42.82 \| 26.83/43.17/57.17/67.25/74.67 | 149.01 \| 99.17/151.33/199.17/222.42/271.33 | 191.84 \| 133.83/192.5/248.5/281.42/321.33 | 71.94 \| 50.19/72.19/93.19/105.53/120.5 | 1.744 \| 1.217/1.750/2.259/2.558/2.921 | 42.88 \| 26.83/43.17/57.5/67.25/74.67 |
| All official qualification | 11.97 \| 3.67/9/24.67/42.67/71 | 46.77 \| 8.33/37.33/99.67/164.83/250.67 | 58.74 \| 13.33/47/122.33/199.67/301 | 22.03 \| 5/17.62/45.88/74.88/112.88 | 0.534 \| 0.121/0.427/1.112/1.815/2.736 | 12.20 \| 3.67/9.33/25/43/71 |
| All official playoff | 17.89 \| 6.33/14.67/34.33/55.33/74.67 | 65.24 \| 19.67/56.33/124.33/191.36/275.33 | 83.13 \| 28/71.67/156/237.36/321.33 | 31.17 \| 10.5/26.88/58.5/89.01/120.5 | 0.756 \| 0.255/0.652/1.418/2.158/2.921 | 18.19 \| 6.67/15/34.67/55.36/74.67 |

### TBA component OPR (COPR)

TBA COPRs are event-team regression estimates and cannot be separated into qualification versus playoff values from this file. The cohort split below is by event. Rates use the full-match 160-second and active-hub 110-second denominators.

| Event cohort | Event-team n | Total fuel COPR | Auto fuel COPR | Teleop fuel COPR | Fuel/match-minute | Fuel/active second | Auto points COPR |
|---|---:|---:|---:|---:|---:|---:|---:|
| Early | 3,554 | 40.56 \| 2.39/25.36/100.98/217.78/409.50 | 8.61 \| 1.23/6.22/18.62/45.96/90.80 | 31.96 \| -1.01/19.47/83.71/178.21/318.70 | 15.21 \| 0.90/9.51/37.87/81.67/153.56 | 0.369 \| 0.022/0.231/0.918/1.980/3.723 | 8.83 \| 1.23/6.39/19.54/46.22/90.80 |
| Championship | 597 | 135.73 \| 46.49/132.34/225.36/325.27/422.97 | 27.30 \| 8.53/24.95/49.28/72.01/99.24 | 108.44 \| 31.89/107.21/183.85/264.67/354.21 | 50.90 \| 17.43/49.63/84.51/121.98/158.61 | 1.234 \| 0.423/1.203/2.049/2.957/3.845 | 27.60 \| 8.86/25.39/49.94/71.82/99.55 |
| All official | 8,160 | 59.07 \| 4.28/36/150.50/269.25/472.14 | 12.04 \| 1.71/8.12/28.39/58.46/99.24 | 47.04 \| 0.31/28.75/121.98/214.71/392.21 | 22.15 \| 1.60/13.50/56.44/100.97/177.05 | 0.537 \| 0.039/0.327/1.368/2.448/4.292 | 12.27 \| 1.73/8.33/28.60/58.39/99.55 |

Negative values and values above alliance observations are a property of unconstrained regression, not literal negative scoring or proof a robot scored 472 fuel. Clamp negative COPR-derived app estimates to zero, but retain the raw value and uncertainty for diagnostics.

### Statbotics whole-season robot distribution

Across 3,724 team-year records:

| Metric | Mean | p10 | p50 | p90 | p99 | Max |
|---|---:|---:|---:|---:|---:|---:|
| Total fuel EPA | 54.77 | 13.44 | 36.52 | 125.50 | 230.63 | 358.51 |
| Auto fuel EPA | 11.78 | 3.84 | 8.52 | 24.59 | 53.56 | 83.59 |
| Teleop fuel EPA | 28.86 | 4.42 | 18.37 | 69.99 | 128.81 | 195.92 |
| Total-points EPA | 55.37 | 13.76 | 37.17 | 126.21 | 230.53 | 356.94 |
| Auto-points EPA | 12.02 | 3.86 | 8.70 | 25.10 | 53.96 | 83.88 |
| Total-tower EPA | 0.61 | -0.68 | 0.08 | 1.82 | 12.01 | 31.26 |
| Fuel per 160 s match-minute | 20.54 | 5.04 | 13.69 | 47.06 | 86.49 | 134.44 |
| Fuel per 110 s active-hub second | 0.498 | 0.122 | 0.332 | 1.141 | 2.097 | 3.259 |

EPA components can also be negative because they are model estimates. They are suitable priors, not literal match counts.

### Tower outcomes per robot appearance

These are direct official outcomes, not regression estimates.

| Cohort | Robot appearances | None | Endgame L1 | Endgame L2 | Endgame L3 | Any endgame climb | Auto L1 |
|---|---:|---:|---:|---:|---:|---:|---:|
| Early qualification | 40,164 | 95.984% | 3.546% | 0.252% | 0.219% | 4.016% | 1.486% |
| Early playoff | 9,030 | 95.714% | 3.776% | 0.210% | 0.299% | 4.286% | 2.447% |
| Championship qualification | 5,982 | 98.312% | 1.454% | 0.117% | 0.117% | 1.688% | 2.006% |
| Championship playoff | 828 | 99.275% | 0.362% | 0.000% | 0.362% | 0.725% | 0.362% |
| All official qualification | 91,056 | 96.545% | 2.987% | 0.260% | 0.208% | 3.455% | 1.554% |
| All official playoff | 19,176 | 96.689% | 2.863% | 0.193% | 0.256% | 3.311% | 2.013% |

The small tower rates are real in this dataset; a high-value climb system was rare. At the team level the distribution is heterogeneous: many teams never climbed, while specialist robots could have high success. Do not cap a team probability at the population rate.

## 3. Elite robots

### TBA COPR

To avoid listing the same robot repeatedly, event COPRs were averaged per team using that team's official match appearances at each event as weights. The top-1% threshold across unique teams is 221.60 weighted total-fuel COPR, or 83.10 fuel per match-minute.

| Rank | Team | Weighted total fuel COPR | Fuel/match-minute | Auto fuel | Teleop fuel | Events / appearances |
|---:|---|---:|---:|---:|---:|---:|
| 1 | 1690 Orbit | 398.33 | 149.37 | 75.56 | 322.77 | 3 / 47 |
| 2 | 4414 HighTide | 364.09 | 136.54 | 66.86 | 297.23 | 4 / 66 |
| 3 | 254 The Cheesy Poofs | 350.61 | 131.48 | 67.89 | 282.72 | 4 / 67 |
| 4 | 1323 MadTown Robotics | 329.27 | 123.48 | 64.35 | 264.92 | 4 / 67 |
| 5 | 2056 OP Robotics | 317.18 | 118.94 | 82.34 | 234.84 | 4 / 69 |
| 6 | 9483 Overcharge | 306.81 | 115.05 | 59.57 | 247.25 | 3 / 44 |
| 7 | 2481 Roboteers | 304.91 | 114.34 | 69.94 | 234.97 | 3 / 50 |
| 8 | 1678 Citrus Circuits | 304.04 | 114.01 | 67.04 | 237.00 | 4 / 68 |
| 9 | 7769 The CREW | 298.86 | 112.07 | 59.74 | 239.11 | 4 / 66 |
| 10 | 27 Team RUSH | 295.53 | 110.82 | 73.77 | 221.76 | 5 / 85 |

COPR puts 254 third. Its omitted four Einstein appearances had no event COPR in the source, which is why the weighted table uses 67 rather than all 71 official appearances.

### Statbotics EPA

The Statbotics top-1% thresholds are 230.53 total-points EPA, 230.63 total-fuel EPA, and 53.96 auto-points EPA. The top ten by total-points EPA are:

| Rank | Team | Total-points EPA | Total fuel | Auto points | Teleop fuel | Total tower |
|---:|---|---:|---:|---:|---:|---:|
| 1 | 4414 HighTide | 356.94 | 358.51 | 75.05 | 195.92 | -1.56 |
| 2 | 254 The Cheesy Poofs | 328.11 | 327.94 | 78.45 | 169.91 | 0.17 |
| 3 | 7769 The CREW | 311.64 | 311.47 | 67.45 | 175.35 | 0.16 |
| 4 | 1323 MadTown Robotics | 309.97 | 311.66 | 73.82 | 169.55 | -1.69 |
| 5 | 2056 OP Robotics | 302.17 | 302.19 | 83.88 | 168.99 | -0.02 |
| 6 | 1690 Orbit | 295.43 | 294.89 | 67.45 | 165.92 | 0.53 |
| 7 | 27 Team RUSH | 295.14 | 295.09 | 73.95 | 154.57 | 0.06 |
| 8 | 1114 Simbotics | 287.57 | 287.94 | 73.13 | 140.52 | -0.37 |
| 9 | 2481 Roboteers | 285.04 | 284.98 | 62.63 | 152.81 | 0.06 |
| 10 | 5687 The Outliers | 282.70 | 282.58 | 73.98 | 155.12 | 0.12 |

COPR and EPA agree on the elite set but not the exact order or magnitude. That is expected: COPR is an event-level linear decomposition, while EPA is sequential and includes priors/model shrinkage.

## 4. Rules ceilings and physical context

### Actual hard constraints

- **Fuel possession:** no limit after match start. There is no legal robot hopper-capacity ceiling.
- **Preload:** at most 8 fuel per robot.
- **Staged supply:** 504 fuel, with the neutral-zone count allowed to vary by about ±24; up to 600 at District Championship or FIRST Championship.
- **Fuel points:** 1 per fuel through an active hub. Fuel scored into an inactive hub earns zero.
- **Hub-active time:** 20 s auto, 10 s transition, two 25 s alliance shifts, and 30 s endgame = 110 active seconds per alliance. Only 90 of 140 teleop seconds are active.
- **Tower per robot:** auto is L1 only, 15 points; teleop is one of L1/L2/L3 = 10/20/30. One robot can earn at most 45 tower points across auto and teleop.
- **Tower per alliance:** at most two auto L1 climbs, so `2×15 + 3×30 = 120` tower points.
- **Auto total:** there is no finite rules cap because fuel can be acquired and scored, and processed fuel returns to the field. The hard tower component is 15 per robot and 30 per alliance.

The data do not violate these rules. The observed 964 fuel exceeds the initial 600-fuel Championship supply because fuel recirculates. The observed alliance tower maximum is 80, below the 120-point hard ceiling. Some COPRs are negative or exceed direct alliance extrema; those are regression artifacts, not rule violations.

### Physics is a warning band, not a hard rule

Public robot reports provide context, but most are design goals or self-reports rather than audited match measurements:

- 1501 reported a 30–35 fuel hopper, 5–8 fuel/s shooting, 38–50 auto fuel, and a 2 s L1 climb ([Chief Delphi](https://www.chiefdelphi.com/t/team-thrust-1501-2026-rebuilt-robot-promo-reveal-frc/516040)).
- 2079 reported approximately 50 fuel capacity and 8 fuel/s throughput ([Chief Delphi](https://www.chiefdelphi.com/t/frc-2079-alarm-robotics-2026-rebuilt-build-thread/509762)).
- A theoretical packaging analysis estimated roughly 60–75 fuel capacity and 2.38–3.02 fuel/s to fill or empty that hopper in a 25 s shift ([Chief Delphi](https://www.chiefdelphi.com/t/some-theoretical-maximum-fuel-capacity-estimation-and-cycle-time-estimations/510848)). This is not a rule limit.
- 1710's design target was 12 fuel/s for 40 shooting seconds at >75% accuracy, or 360 scored fuel ([Chief Delphi](https://www.chiefdelphi.com/t/frc-team-1710-2026-build-thread-open-alliance/507939/16)). It is explicitly an ambitious design goal, not a season average.
- A practical scouting proposal recommended measuring seconds spent shooting and multiplying by robot fuel/s instead of manually counting every ball ([Chief Delphi](https://www.chiefdelphi.com/t/the-easiest-way-to-scout-rebuilt-so-far/510951)).

These rates explain why 100+ fuel per match-minute is physically plausible for an elite robot even though the whole-season p99 is 86.49. They do not justify a universal hard cap.

## 5. Recommended app bounds and zero-data estimates

### Validation policy

Use three states: **valid**, **review**, and **invalid**. `review` should not block entry. Bounds should be conditioned on event cohort when possible.

| App metric | Canonical definition | Invalid | Review / implausible | Zero-scouting estimate |
|---|---|---|---|---|
| `fuel_scoring_rate` | Total successful fuel per 160-second match-minute | `<0`; non-finite | `>86.49` is above Statbotics p99; `>100.97` is above event-team COPR p99; `>150` is beyond the weighted elite leaders but still not rules-impossible. Keep 180 only as an emergency UI guardrail, not a claimed physical maximum. | Preferred: `Statbotics total_fuel EPA × 60/160`. Fallback: nonnegative, reliability-weighted TBA `Hub Total Fuel Count` COPR × `60/160`. Shrink low-match estimates toward the cohort median, not a 0–100 rating subscore. |
| `cycle_time_sec` | Choose one meaning. Recommended stored metric: **batch intake-to-score cycle**, measured from scouting/video. | `<=0`; non-finite; `>160` | `<1 s` or `>90 s` should be reviewed for a batch cycle. These are operational bounds, not dataset-derived physical limits. | `null`. EPA/COPR contains no batch count, hopper fill, or intake-to-score timestamps, so it cannot identify batch cycle time. If the product instead means **seconds per scored fuel**, rename it and compute exactly `160/total_fuel` or `140/teleop_fuel`; do not estimate it independently. |
| `auto_contribution` | Robot auto points: active-hub fuel + 15 if auto L1 | `<0`; non-finite | `>53.96` is above Statbotics p99; `>83.88` is above Statbotics max; `>99.55` is above event-team COPR max. These are review thresholds, not hard rules caps. | Preferred: Statbotics `auto_points` EPA. Fallback: nonnegative TBA `totalAutoPoints` COPR. For a completely unseen 2026 team, use the 8.70 season median with a wide interval, or show unknown. |
| `climb_success_prob` | Probability of any official endgame L1/L2/L3 result; keep level probabilities separately | Outside `[0,1]`; non-finite | No interior value is physically impossible. Flag small-sample confidence, not high probabilities: specialist teams can legitimately approach 1. | Use team-specific official outcomes with a Beta-binomial prior centered on the applicable cohort rate (3.455% official qualification overall). Keep auto L1 separate. If the team has no 2026 appearances, use the cohort prior; do not map tower EPA or a generic endgame rating linearly to probability. |

For the Beta-binomial climb estimate, a transparent 20-appearance prior gives `alpha=0.691`, `beta=19.309` for the season-wide 3.455% rate. Report both observed `successes/n` and the smoothed estimate. This prevents `0/1` from displaying as certain zero without preventing a long, genuine success history from approaching 100%.

### Important schema decision

The current app conflates two different cycle concepts:

1. `teleop_seconds / fuel_count`, which is **seconds per fuel**, and
2. “average time between intake and confirmed score,” which is a **batch/cycle duration**.

REBUILT robots can intake while driving or shooting and can carry dozens of fuel. A 30-fuel scoring burst is one operational cycle but 30 scored elements. These metrics cannot share a field. Store `sec_per_scored_fuel` as the reciprocal of the chosen rate and `batch_cycle_time_sec` only from direct scouting/video.

## Team 254 and the current estimator

### Actual 2026 evidence

Statbotics ranks 254 second of 3,724 teams with 328.11 total-points EPA. Its breakdown is:

- total fuel 327.94: 78.12 auto fuel + 169.91 teleop fuel, including 27.28 transition, 70.36 first active shift, 72.27 second active shift, and 79.91 endgame fuel;
- auto points 78.45;
- total tower EPA 0.17 (auto tower 0.33, endgame tower -0.16; small negative components are model artifacts);
- full-match fuel rate 327.94 × 60/160 = **122.98 fuel/min**;
- active-hub rate 327.94/110 = **2.981 fuel/s**;
- full-teleop rate 169.91 × 60/140 = **72.82 fuel/min**.

Its appearance-weighted official-event TBA COPRs are 350.61 total fuel, 67.89 auto fuel, 282.72 teleop fuel, 68.11 auto points, and 0.13 tower points. That corresponds to **131.48 total fuel per match-minute**. Event total-fuel COPRs were 367.62 (Silicon Valley), 327.18 (Central Valley), 384.30 (California Northern Championship), and 317.44 (Curie).

Direct official outcomes show **0 auto climbs and 0 endgame climbs in 71 appearances**.

### What is wrong with the four displayed estimates

| Displayed value | Finding |
|---|---|
| **144 fuel/min** | It is not impossible—elite COPR reaches 149.37—but it is not 254's evidence-backed estimate. It is 17.1% above 254's Statbotics full-match rate (122.98) and 9.5% above its weighted COPR rate (131.48). More importantly, `fuel_scoring_rate` is labeled as a teleop metric elsewhere in the app, while `_model_derived_averages_from_rating` combines auto and teleop phase rates. Against 254's full-teleop rate of 72.82/min, 144 is 97.7% high. The denominator must be fixed in the API/UI. |
| **34 s cycle time** | If this means seconds per fuel, it is wrong by roughly two orders of magnitude: 254's Statbotics reciprocal is 160/327.94 = 0.488 s/fuel for the whole match, or 140/169.91 = 0.824 s/fuel in teleop. It is also internally inconsistent with 144 fuel/min, whose reciprocal is 0.417 s/fuel. If it means a batch intake-to-score cycle, neither COPR nor EPA can estimate it, so 34 s is unsupported and should be `null` without scouting. |
| **57 auto points** | Plausible but materially low: 27.3% below Statbotics auto-points EPA 78.45 and 16.3% below weighted TBA auto-points COPR 68.11. Use the actual component estimate, not a rating-to-points transformation. |
| **0% climb** | The observed value is factually aligned with 0/71 official auto and endgame climbs. What is wrong is treating it as a certainty or deriving it from a generic endgame subscore. With no successes in 71 trials, the one-sided 95% binomial upper bound is about 4.1%; the suggested 20-appearance empirical-Bayes prior gives about 0.76%. Show `0/71 observed` and an interval/smoothed estimate. |

### Code-level causes

In `backend/app/api/routes_teams.py::_model_derived_averages_from_rating`:

- a raw `bps_median` is multiplied by 60 and otherwise a 0–100 throughput score is converted to fuel/min by calibration or an arbitrary fallback;
- cycle time is independently set to `55 - 0.42×throughput`, clamped to 12–65 s, so it is not the reciprocal of the rate and has no REBUILT batch-cycle evidence;
- raw `auto_points_est` is accepted without a meaningful game-aware check, while the fallback maps a 0–100 subscore to only 0–12 points (clamped at 18), despite a season p99 of 53.96 and 254 at 78.45;
- climb success is `endgame_subscore/100`, which is not a calibrated probability.

In `backend/app/services/calibration/fuel_rate.py`:

- the default teleop relation `6 + 1.08×throughput_score` and auto relation `0.27×auto_score` are rating mappings, not game-component estimates;
- auto **points** are converted to a per-minute rate and blended with teleop **fuel**, mixing units whenever auto tower points are present;
- the time-weighted result is a whole-match average, but the product schema describes fuel rate as teleop scoring;
- failed calibration silently falls back to those constants, hiding whether a value is data-backed;
- the calibration inherits upstream official-alliance allocation/equal-split estimates, so fitting it does not create new robot-level ground truth.

The fix is not a better slope. Use the external component directly, attach its source and denominator, shrink by sample size, and leave non-identifiable fields such as batch cycle time unknown.

## Implementation recommendations

1. Add explicit denominator metadata: `fuel_per_full_match_min`, `fuel_per_full_teleop_min`, and optionally `fuel_per_active_hub_sec`. Do not expose all three as an unlabeled “fuel rate.”
2. Replace rating-derived zero-scout estimates with ordered sources: direct scouting → Statbotics component EPA → TBA event component OPR → cohort prior.
3. Store source, sample size, event/week, and uncertainty with every estimate. Never present a prior-derived number as a measured average.
4. Split `cycle_time_sec` into `batch_cycle_time_sec` and `sec_per_scored_fuel`. Only the latter can be derived from fuel totals.
5. Model climbs as categorical outcomes (`None`, `L1`, `L2`, `L3`) plus a separate auto-L1 probability. “Any climb” loses 10/20/30-point information.
6. Use the cohort tables above for warning bands. Championship playoff values should not be validated against early-qualification p99.
7. Keep hard validation limited to domain errors and actual rules constraints. High fuel and auto values should trigger review, not rejection, because the rules have no finite fuel-scoring cap.

## Artifacts

The analysis script and its JSON output lived in a scratch directory and are not kept in the repository; the
numbers above can be regenerated from TBA's `/event/{key}/matches` and `/event/{key}/coprs` for the 2026 season.
