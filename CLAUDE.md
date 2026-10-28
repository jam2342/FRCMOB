# FRCMOB: FRC scouting & match-analysis platform

Scouting and analytics for **FRC** (FIRST Robotics Competition), 2026 game **REBUILT**: 160 s match (20 s auto), fuel into hubs that alternate active shifts, endgame climb. The game manual is copyrighted by FIRST and never goes in git (`docs/game_manual.txt` is ignored). Audience: casual fans **and** competing teams.

Official data (TBA score breakdowns, FRC Events) feeds ratings, predictions, forecasts, pick values and picklists. Teams get private join-code **workspaces**: pit + match scouting, live team rooms, picklists, assignments. A phone **match recorder** (in-browser detector + 4-tap field calibration) gives field tracks, heat maps and offense/defense once an operator accepts the recording. Broadcast-video analysis was retired 2026-09-28 (it can't tell robots apart).

**This file is public** (the repo is open source) and loads every session. Keep it to current facts about the code. Hosts, access, open items and personal notes belong in the maintainers' private notes, never here. Docs index: [docs/README.md](docs/README.md).

---

## Architecture

| Part | Path | Stack |
|---|---|---|
| Backend API + scheduler | `backend/` | FastAPI, SQLAlchemy 2.1 + psycopg3, Pydantic v2, Alembic, APScheduler (Redis lock across workers) |
| Frontend (PWA + Capacitor iOS/Android) | `ScoutingApp/` | React 19, Vite, TypeScript, react-router (HashRouter) |
| Infra | `docker/` | `docker-compose.yml` (local), `.prod.yml`, `.oci.yml` (prod), Caddyfile, backup/deploy/restore scripts |

**Production:** the frontend on Vercel (`ScoutingApp/vercel.json` rewrites `/api/*` to the backend), the backend + Postgres 17 + Redis 7 + Caddy on one VM (`docker/docker-compose.oci.yml`). Merging to `main` deploys both: Vercel builds the frontend, and `.github/workflows/deploy.yml` ships `backend/`/`docker/` changes after CI. See `docs/BACKEND_HOST.md`, `docs/FRONTEND_HOST.md`, `docs/PROD_RUNBOOK.md`.

**Backend (`backend/app/`):**
- `api/routes_*.py` per domain; `edge_cache.py` (every public GET classified public/private by a test; classify new routes), `body_limit.py`.
- `services/events/`: `ingest.py` (TBA → DB), `live_ingest.py` (every 3 min for events in play), `first_live_results.py` (FRC Events results ~1 min before TBA), `match_lineups.py` (lineups replaced, never merged), `regional_automation.py` (12 h refresh, checked every 30 min, decided from Redis), `win_probability.py`.
- `services/ratings/`: rating_v16 team model (`team_model.py`, Kalman on alliance totals; `team_model_service.season_run()` replays the season, cached per worker, ~20 s cold) + `win_correction.py` (numpy MLP; live label `team_model_corrected_v4`), `pick_value.py`, `ranking_points.py`. Results per phase: `docs/RATING_ROADMAP.md`.
- `services/simulation/`: event forecast (2,000 runs). `services/scoring/`: TBA breakdown → per-robot truth + `match_alliance_results`. `services/seasons.py`: rated / frozen seasons.
- `services/scouting_rooms/`, `workspaces.py`: team rooms (one per workspace+event), WS + Redis pub/sub. `services/auto_scout/`: drafts + `shift_play.py` offense/defense from accepted recordings.
- `db/models.py` (all ORM models), `core/config.py` (settings), `game_config/` (`season_template.json`: per-season rules).

**Frontend (`ScoutingApp/src/`):** `api.ts` (the one API client, saved copies, offline fallback), `pages/` (route screens + `*.helpers.ts` + tests), `features/` (one folder per feature), `components/`, `styles/` (`styles/README.md` rule 5 = the uniformity laws: one owner per kind of UI), `platform/` (runtime, `safeStorage`), `config/season.ts` (season snapshot, tested against `season_template.json`).

**Detector:** runs only on phones. v4: `frc_robot_detector_v4_s.onnx` on WebGPU (holdout mAP@0.5 0.779), `_n` on WASM (0.740), cutoffs in `modelArtifact.ts`. **Weights are not in git:** `ScoutingApp/scripts/fetch-models.mjs` downloads them into `public/models/` before a build when `ONDEVICE_MODEL_SOURCE` is set, checked against `build/modelChecksums.json`; without them the build warns (Vercel builds fail) and on-device detection is off. A new detector = new filename + checksum. Datasets and `.pt` weights live outside the repo (`docs/TRAINING.md`). **Locked holdout** `frc_einstein_2026_holdout_reviewed_yolo` is test-only; never train or split on it. The old "0.98" was leakage; never cite it.

---

## Commands

```bash
docker compose -f docker/docker-compose.yml up        # local postgres, redis, backend:8000
cd backend && APP_ENV=development pytest               # a production-like .env makes 47 tests 503 without this
cd backend && .venv/bin/python -m ruff check .         # python -m: .venv shebangs go stale when the repo moves
cd ScoutingApp && npm run dev | build | test | lint
npm run guard:uniformity                               # browser guards: GUARD_BROWSER_CHANNEL=chrome
npm run e2e:regression
```

- *Rating change:* `scripts/rebuild_season_ratings.py --season 2026` (~1 min); prove refactors with old-vs-new on fresh copies of real data.
- *New season:* `docs/SEASON_TRANSITION.md` + `scripts/season_readiness.py --season 2027`; freeze the old one with `scripts/freeze_season.py`; build priors with `scripts/build_prior_strengths.py`.

---

## Rules and traps

**Code style**
- No docstrings. Short `#` comments that say why. Match surrounding code.
- `DATABASE_URL` uses `postgresql+psycopg://`.
- Season assumptions live in `game_config/`, `services/scoring/breakdown.py`, `ratings/constants.py` (`SEASON_BASELINES`; a season without a row fails closed), `ratings/official_points.py`, `auto_scout/specs.py`, `config/season.ts`.
- One look per kind of UI (`styles/README.md` rule 5), enforced by `src/consistency.test.ts` + `guard:uniformity`. New CSS uses tokens (`guard:colours`). Must-win phone sizes go in a CSS module, since the unlayered `touch-polish.css` beats layered rules.
- Mobile is first-class: check 390 px and 250×450. Use `storageGet/Set` for preferences, never for scouting saves (those must report failure).
- A new external origin (script, frame, socket, API host) must be added to `build/contentSecurityPolicy.ts` or it's blocked.
- Page tours (`tutorial/tutorialBlueprints.ts`) point at `data-tour` hooks (`tourId` on `SurfaceCard`/`SegmentedTabs`), never class names. Each page has its own tour or none (`layout/appUx.ts`); the blueprint test fails on a hook no page renders.
- Tests: conftest turns off `STALE_READS`, `FIRST_LIVE_RESULTS`, `CORRECTION_BLEND`, and lifts the workspace creation budget.

**Data and domain**
- Ratings reset each season; earlier events then the season's typical robot fill in. Penalties aren't in the rating (the breakdown doesn't say who fouled).
- Climb truth comes from the TBA breakdown. Check `docs/REBUILT_PLAUSIBILITY.md` before calling a number wrong (climbs are rare; elite ≈130 fuel per active minute).
- Statbotics is gone. TBA 404s for B-team keys and future events are normal. B teams: TBA lowercases (`frc100b`), our teams table keeps `frc100B`, so always write the stored spelling.
- TBA payload shapes (alliances, knockout brackets, score breakdowns) change by event and season: test parsers against real payloads, not mocked happy paths.
- `getEventRatings(key, true)` is an admin-only rebuild; poll with `{ bypassCache: true }`.
- Live ratings poll on purpose (venue Wi-Fi); team rooms use WS.
- Room creation never needs admin. Workspace leaders own team rooms; removal is immediate (WS closes 4401).
- Pit scouting keeps "claimed" capabilities on purpose.

**Security**
- The repo is public. Never commit credentials, `.env` files, private notes, weights, datasets, recordings, database exports or FIRST manuals ([docs/PUBLISHING.md](docs/PUBLISHING.md)).
- Room/presence bugs only show with 2 workers + Redis; test those paths that way.
