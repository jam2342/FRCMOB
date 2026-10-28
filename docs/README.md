# Docs

Living documents: keep these true when the thing they describe changes. Dated reports, audits and evidence go in [`archive/`](archive/) by month and are not edited afterwards.

## Running production
- [PROD_RUNBOOK.md](PROD_RUNBOOK.md): health checks, event days, what to watch
- [BACKEND_HOST.md](BACKEND_HOST.md): the OCI VM, deploys, migrations, backups, pit-photo restore
- [FRONTEND_HOST.md](FRONTEND_HOST.md): what the frontend host must do; checklist for leaving Vercel
- [DISASTER_RECOVERY.md](DISASTER_RECOVERY.md): rebuilding from the Neon off-site copy
- [OCI_ENV_VARS.md](OCI_ENV_VARS.md): backend environment variables

## Ratings and the season
- [RATING_ROADMAP.md](RATING_ROADMAP.md): the rating model, phase by phase, with results
- [REBUILT_PLAUSIBILITY.md](REBUILT_PLAUSIBILITY.md): real 2026 ranges; check before calling a number wrong
- [SEASON_TRANSITION.md](SEASON_TRANSITION.md): what to change for a new game, kickoff timeline
- `game_manual.txt`: the 2026 game manual (not in git)

## Detector
- [TRAINING.md](TRAINING.md): training and evaluating the on-device detector. Datasets and weights are in the OCI bucket under `archive/training-media-2026-10-26/`.
- [LABELING_STANDARD.md](LABELING_STANDARD.md): how robots are labelled

## Apps and offline
- [NATIVE_APP.md](NATIVE_APP.md), [NATIVE_BETA.md](NATIVE_BETA.md): Capacitor iOS/Android shells and beta gates
- [OFFLINE_PHONE_PILOT.md](OFFLINE_PHONE_PILOT.md): the real-phone offline check

## Plans
- [ACCOUNTS_PLAN.md](ACCOUNTS_PLAN.md): optional email sign-in (waiting on a domain)

## Legal and publishing
- [THIRD_PARTY_ASSETS.md](THIRD_PARTY_ASSETS.md): third-party terms and how the app meets them
- [PUBLISHING.md](PUBLISHING.md): keeping a public repo safe (what never goes in, leak checks, what to do after a leak)
