# Regression checks

Each check drives the real app in a browser through a failure real users hit,
found by the QA round of 2026-10-16 (PRs #181–#183): scouting reports that never
reached the team, white screens on bad wifi, sheets stuck over the page, "0 matches"
shown during an outage. A check fails if that bug comes back.

```bash
npm run e2e:regression                     # build, serve, run all (~10 min)
npm run e2e:regression -- --only network   # one area, or any part of a check's name
PW_CHANNEL=chrome npm run e2e:regression   # real Chrome instead of Playwright's Chromium
PW_ENGINE=webkit npm run e2e:regression -- --only pit   # Safari's engine (WebKit); see below
REGRESSION_BASE=http://127.0.0.1:5199 npm run e2e:regression   # an already running build
```

- **Data:** public reads go to production through `vite.regression.config.ts`, a
  read-only proxy (writes get 403). Signed-in flows (team room, pit, picklist) use the
  stateful mocks in `mocks/`, shaped like the backend's responses; keep them faithful
  when the API changes, or checks fail for reasons users never see.
- **Faults** (server errors, stalls, failed downloads, offline) are injected with
  Playwright routes in each check.
- **CI:** `.github/workflows/regression.yml` runs nightly, on demand, and on PRs that
  touch the frontend. A failing check is retried once; failures leave screenshots in
  `out/` (uploaded as the `regression-failures` artifact).
- **Adding a check:** add `{ name, run(browser) }` to `scouting.mjs`, `navigation.mjs`
  or `network.mjs`. Name it after what the user would notice, use `phone()` (sets GPC
  so no real telemetry is sent) and `until()` instead of fixed sleeps where you can.
- **WebKit** (`PW_ENGINE=webkit`): the pit photo check passes. "a pit draft delivered while the page
  was closed…" fails there (also on `main`): it opens Home offline with the service worker blocked,
  the Home chunk can't load, the error boundary reloads once back online, and Playwright's WebKit
  then serves the failed module again on every reload, so the app never starts. Whether real
  Safari does the same is on the real-device checklist; CI runs Chromium only.
