# UI stress verification — October 1, 2026

## Scope and safety

Tested the locally built web UI with real public event data and disposable private
workspace fixtures. Public requests were credential-free GETs; every write and
private request was intercepted locally. No production data was changed and no
code was published. Service workers were blocked in the visual sweep; the separate
offline test exercised the installed service worker and cold recorder startup.

Chrome profiles: 320×720 dark phone, 390×844 light phone, 844×390 landscape,
768×1024 light tablet, 1440×900 dark/light desktop, and a 390×844 phone with doubled
font tokens. WebKit covered the smallest phone and desktop. These are browser
viewports, not physical-device certification or an OS text-size test.

The sweep exercised populated routes and tabs, long legal workspace/member names,
long warnings, workspace dialogs, mobile menus, repeated theme/density changes,
refresh-input bounds, 40 rapid route changes, server errors, and failed API
connections. It checked page exceptions, crash screens, empty renders, and document
horizontal overflow. Screenshots were also inspected manually. Passing these checks
does not prove every possible interaction or accessibility criterion.

## Bugs found and repaired

- Favorites repeatedly navigated even when its query string was unchanged. Leaving
  the page could trap the renderer in a loop. The writer now compares before
  navigating, and shared URL-sync controls retain their identity across renders.
- Long workspace/member names overflowed the identity banner and Leave button.
  Names now wrap; the button uses a stable label and the dialog title wraps.
- Long coverage quality warnings forced the page wider than the viewport. Their
  flex children can now shrink and wrap.
- Doubled text cramped Match Center alliance lineups and overflowed context/hero
  content. Narrow phones now place the score above the alliances; the grid and
  flex sizing allow content to fit without hiding it.

## Final results

- Chrome: **316 checks, 0 failures**, across all seven profiles.
- WebKit: **89 checks, 0 failures**, smallest phone and desktop.
- Targeted doubled-text reruns: 14 checks and 12 checks, both with 0 failures.
- No page exceptions, crash screens, or document overflow above the 2 px tolerance
  occurred in the final sweeps. Each main sweep fetched 65 public responses;
  neither issued a write.
- Frontend: **462 tests passed** across 85 files; lint and production build passed.
- Normal iOS simulator build, Android debug APK, and unsigned release AAB passed.
  Both detector model hashes match source in every package. iOS signature validation
  passed and simulator-only probe markers are absent from its executable. The
  rebuilt app was installed, launched, and its screenshot inspected.
- Removed **483.1 MiB** of regenerable build intermediates; kept APK/AAB/iOS outputs.

Evidence is in `/tmp/frcmob-stress-final/report.json`,
`/tmp/frcmob-stress-webkit-final/report.json`, and
`/tmp/frcmob-stress-visual/`; logs use the corresponding `/tmp/frcmob-*.log` paths.
These local scratch artifacts are not committed. The reusable harnesses are.

## Recording and workspace fault test

`e2e/validate-workspace-recording.mjs` passed: rapid double-click Join creates one
request; the existing recording is pinned to the joined workspace before upload;
a connection reset retains it; a 409 retains it without automatic conflict replay;
a confirmed retry uses the same recording/workspace and marks it synced only after
success; revoked workspace access clears credentials while retaining the recording.
All server responses in this test are local fixtures, not live-server integration.

`e2e/validate-offline-sync.mjs` also passed its conflict retention, credential-free
export, offline preparation, and cold isolated-recorder checks.

## Reproduce

From `ScoutingApp/`, build with `npm run build`, then serve with
`npm run preview -- --host 127.0.0.1 --port 4179 --strictPort`.

```sh
npm run validate:ui-stress
STRESS_BROWSER=webkit STRESS_QUICK=1 npm run validate:ui-stress
npm run validate:workspace-recording
node e2e/validate-offline-sync.mjs
```

Install Playwright WebKit if needed. The stress harness rejects non-local target
hosts. `STRESS_OUTPUT` selects the report/screenshot directory; `STRESS_ROUTES`
and `STRESS_PROFILES` permit targeted reruns.

## Live-data observation

The public Archimedes schedule returned prediction source `blended_det_ml_v1`
and model `match_outcome_torch_20261001_052429`. This differs from the September 30
project note that ML should remain inactive. The UI reports the received source;
this work did not change model activation, blending settings, or credentials.
