# FRCMOB

FRCMOB is an open-source scouting and match-analysis platform for the FIRST Robotics Competition. It combines manual scouting, live scouting rooms, team and alliance analytics, match predictions, and an on-device match recorder that runs an FRC robot detector in the browser, maps robots onto the field from a four-tap calibration, and turns a recorded match into heat maps and offense/defense profiles.

FRCMOB is an independent community project. It is not affiliated with, endorsed by, or sponsored by FIRST. FIRST®, FIRST Robotics Competition®, and related marks and game materials belong to For Inspiration and Recognition of Science and Technology (FIRST).

## What is included

- `ScoutingApp/`: React + TypeScript web/PWA frontend and Capacitor iOS/Android projects
- `backend/`: FastAPI API, official-data ingest, ratings and ML, scheduler, database models, and tests
- `game_config/`: season-specific field and scoring configuration
- `docker/`: local and production Compose definitions

Model weights, training datasets, match recordings, database contents, credentials, and FIRST game manuals are intentionally not distributed. See [Third-party assets](docs/THIRD_PARTY_ASSETS.md).

## Native app

The iOS and Android foundation bundles the frontend and on-device detector for offline startup. Build commands, platform requirements, verification, and remaining beta-release work are in [Native app setup](docs/NATIVE_APP.md).

## Local development

Requirements: Node.js 20+, Python 3.11+, PostgreSQL 16, and Redis 7. Detector training needs `backend/requirements-training.txt` on top.

1. Copy `.env.example` to `.env`. The defaults describe local development; generate secrets before enabling admin access.
2. Start PostgreSQL and Redis with `docker compose -f docker/docker-compose.yml up postgres redis -d`.
3. Install and start the API:

   ```bash
   cd backend
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   PYTHONPATH=. alembic upgrade head
   PYTHONPATH=. uvicorn app.main:app --reload
   ```

4. In another terminal, install and start the web app:

   ```bash
   cd ScoutingApp
   npm ci
   npm run dev
   ```

The app works without external API keys, but event ingestion and some enrichment features require their respective integrations. Video detection requires separately provisioned weights.

## Verification

```bash
cd backend && .venv/bin/ruff check app tests && PYTHONPATH=. .venv/bin/pytest -q tests
cd ScoutingApp && npm ci && npm run lint && npm run test && npm run build
```

CI runs the same lint, test, and build gates. Production deployments should use strict startup validation and must never expose an admin key through a `VITE_` or other client-side environment variable.

## Contributing and security

Read [CONTRIBUTING.md](CONTRIBUTING.md) before sending changes. Report vulnerabilities privately as described in [SECURITY.md](SECURITY.md), not in a public issue.

## License

Copyright © 2026 Jamal Mammadzada and contributors. FRCMOB is licensed under the [GNU Affero General Public License v3.0](LICENSE). Network users must be offered the corresponding source as required by the license.
