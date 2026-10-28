# Independent scouting UI stress test — 2026-10-01

Five additional confirmed issue groups were repaired. Work started on `feat/native-ios-verification` at `40fb69b`; by the final local commit, the shared checkout was on `main`. This session did not switch branches. The concurrent session's separate picklist/recorder commit, `0a976dd`, was preserved as the parent; this report and commit contain only this session's changes. Independent browser exploration preceded reading existing test implementations.

All private requests and mutations used intercepted requests and disposable fixtures. No production writes, push, publishing, credential reads, or secret environment-file reads occurred. Builds and Vitest used an isolated configuration with an empty `envDir`; existing detector assets were reused. Exploration and final verification ran against a separate loopback preview at port 4182.

## Confirmed issues, concrete reproductions, and repairs

| Severity | Reproduction and observed result | Root cause and repair |
| --- | --- | --- |
| High — lost manual scouting edits | Open QM1/FRC254, enter Auto fuel `7`, and immediately navigate to Settings and back. The field returned as `0`. Refreshing or switching context during the 400 ms window could also discard the final edit. | The pending draft timer was cancelled on unmount. Every committed form edit now persists immediately. A loaded-scope guard prevents old values from being written under a newly selected match/team/workspace. |
| High — workspaces shared an unfinished form | Enter `13` for a robot in workspace A. In another tab, switch the device to workspace B while leaving the same match and robot selected. This tab still displayed A's `13`, making it possible to save A's observations as B's report. | Draft loading depended on match and team, omitting workspace. The full workspace/match/team scope now controls loading and persistence, and an in-flight save checks that its original workspace is still active. |
| High — full storage destroyed the scouting screen | Enter counters, inject a quota failure for local report writes, and press Save. React's error boundary replaced the form with “Scouting failed to load / Injected storage quota”. The draft could already have been cleared as if the save succeeded. | Report persistence ran in an uncaught effect after success and draft deletion. Save now confirms the storage write first; failure retains the form and reports a useful error without adding a phantom report or resetting Rapid Tap. Incidental preference writes are guarded too. Failed draft writes recover in memory through route changes and warn before leaving the document. |
| Medium — Rapid Tap retained stale notes | Select Rapid Tap, enter a counter and “Only applicable to this report”, and save. Counters cleared, but the per-game note remained in the next form. | The reset omitted `scoutNotes`. Successful Rapid Tap saves now clear notes along with counters and RP fields. Failed saves retain them. |
| Medium — scouting headers moved or covered controls | At 844×390, focus a counter and show a storage warning. The idle header alternated between hidden/expanded states; Save never settled for a normal click. In desktop WebKit, scrolling to Rapid Tap could move it to `y=-214.5` or under sticky headers, blocking clicks. | Mobile header resizing caused scroll anchoring to feed layout adjustments back into the scroll-direction handler. Anchoring is disabled only on the mobile scouting scroller. Desktop auto-collapse no longer moves setup controls during focus; its explicit collapse button remains. Scrolling accounts for the measured fixed topbar and desktop setup header, and restores the previous scroller styles on route exit. |

The memory fallback preserves unfinished drafts within the current document, not after OS eviction, refresh, or closing. The UI says to keep the tab open, free storage, and retry Save. A confirmed save clears its draft and warning. The entry list uses the latest available state so synchronous persistence does not overwrite reports arriving in the background.

## Verification

Final builds were actually served and exercised in the browsers. The repeatable new suite is `node e2e/stress-scout-recovery.mjs`; it defaults to loopback port 4179, accepts `SCOUT_TEST_URL`, and rejects non-loopback URLs. `SCOUT_TEST_BROWSER=webkit` selects WebKit. Every API request is mocked; unexpected external requests are denied. The existing recovery suite now accepts `USER_URL`, also restricted to loopback, to avoid concurrent preview rebuilds.

- Final independent scouting suite: **93/93 Chrome and 93/93 WebKit**. Seven profiles in each engine: 320 px phone, 390 px phone, landscape, tablet, desktop dark, desktop light, and enlarged text. Covers immediate navigation/reload, Back/Forward, robot/match/workspace isolation, bounded numeric inputs, pasted 600-character notes, all-localStorage-write quota failure, memory-only draft recovery, failed-save retention, successful retries, Rapid Tap resets, saved-report reload, idle header geometry, and desktop focus hit testing. Zero page exceptions and zero production writes.
- Broader responsive sweep: **316/316**, zero failures, 65 credential-free public GETs, all private requests and writes intercepted. This sweep ran during the earlier fixes; its results are context for unaffected routes. A focused final-route sweep after the mobile header repair passed **56/56** across the same seven profiles, including settings, menus, rapid navigation and server/offline recovery. The final desktop header refinement is covered by the complete independent matrix above.
- Existing user-error recovery suite: **40/40 Chrome and 40/40 WebKit**, zero page exceptions. Pit drafts/photos, failed saves, rejected inputs, stale responses, unavailable private reads, and storage recovery continue to pass.
- Workspace-recording regression: passed. Rapid join issued one request; interrupted uploads retained the recording; workspace access remained pinned; rejected recordings did not replay automatically; explicit retry and revocation recovery passed. API persistence was mocked.
- Offline-sync regression: passed. Rejected writes survived reload, recovery export excluded fixture credentials, offline preparation succeeded, and an actual service-worker recorder reload retained cross-origin isolation. No production uploads occurred.
- TypeScript project check, full ESLint, production build, and `git diff --check`: passed. The actual build artifact and representative phone/landscape/desktop screenshots were inspected.
- Full frontend unit suite on the final source: **489 tests across 92 files passed**, exit 0, recorded in `/tmp/frcmob-ui-adversarial-b/tests-complete.log`. Two new storage-recovery unit tests cover the volatile draft, workspace isolation, unload guard, successful persistence retry, and failed deletion without resurrecting an old draft.

## Evidence

All evidence below is under `/tmp/frcmob-ui-adversarial-b/`; temporary evidence may eventually be removed by the OS.

| Evidence | Location |
| --- | --- |
| Lost counter, workspace carryover, and full-storage page failure | `probe-manual.txt`, `before.json`, `manual-rapid-nav-before.png`, `manual-workspace-before.png`, `manual-quota-before.png` |
| Stale Rapid Tap notes | `probe-rapid-notes.txt`, `rapid-notes-before.png` |
| Header feedback loop, before/after | `landscape-jitter-before.json`, `landscape-jitter-after.json`, `probe-landscape.txt`, `probe-landscape-after.txt`, matching PNGs |
| WebKit focus position and hit testing | `webkit-focus-before.json`, `webkit-focus-after.json`, `probe-webkit-focus-verified.txt`, matching PNGs |
| Final Chrome/WebKit matrix and screenshots | `chrome-complete/report.json`, `webkit-complete/report.json`, `chrome-complete.log`, `webkit-complete.log` |
| Broader and focused responsive sweeps | `broad-chrome/report.json`, `broad-scout-final/report.json` |
| Existing recovery suites | `user-errors-chrome/report.json`, `user-errors-webkit/report.json` |
| Workspace and offline regressions | `workspace-recording.log`, `offline-sync.log` |
| Build, typecheck, lint, and unit suite | `build-complete.log`, `typecheck-complete.log`, `lint-complete.log`, `tests-complete.log` |

In the idle landscape sample, the last 20 frames changed Save's position by **11.772 px** before the repair, alternating header classes and document scroll positions. Afterward movement was **0 px**, with one header state and one scroll position. The final WebKit focus probe placed Rapid Tap at `y=339.344`; hit testing reached the tab itself, and normal clicks passed.

## Rejected findings and test corrections

Initial non-hash routes and incomplete response shapes were fixture mistakes and were excluded from application findings. The first quota unit test mocked `Storage.prototype`, while this Node/jsdom setup uses a localStorage shim; the mock was corrected to the actual storage object. A successful retry initially left the new warning visible; that application defect was fixed before acceptance.

The new browser harness initially expected Rapid Tap mode to survive route remounting; it now reselects the mode through Setup before checking its reset. WebKit also confirmed that durable storage can be observed before React finishes rendering a reset; the test waits for the actual rendered state instead of assuming click completion proves it. Normal pointer actionability is retained—no forced clicks bypass the header findings. Representative mid-flow screenshots use the current viewport to avoid changing WebKit layout during the sequence.

Full lint initially failed in the other session's uncommitted hook test. That shared-checkout issue was subsequently repaired; final full lint is clean. This session did not edit or commit the other session's files.

## Coordination and limits

Direct Codex-app access was denied by computer-use policy, and no cross-chat messaging tool was available. Shared local notes were written to `/tmp/frcmob-ui-session-coordination.md` and the other session's evidence directory, identifying owned files, findings, and the lint error. No acknowledgment or joint review is claimed. The separate concurrent report is `docs/UI_INDEPENDENT_STRESS_2026-10-01.md`.

Coverage is bounded browser testing, not a bug-free claim or complete accessibility certification. Physical phones remain deferred. Native archives and simulator runs were not repeated for this round. Browser viewports do not establish real OS keyboards, memory eviction, camera accuracy, thermals, or event-venue networking. Injected failures exercise client recovery; they do not prove live backend persistence or every authorization path. Production was neither modified nor published.
