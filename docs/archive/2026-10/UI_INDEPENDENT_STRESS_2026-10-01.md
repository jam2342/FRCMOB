# Independent UI and recovery stress test — 2026-10-01

Work started on `feat/native-ios-verification` at `40fb69b`. Independent browser exploration preceded reading the existing test implementations. All mutations used intercepted requests and disposable fixtures. Production was not written to or published. Secret environment files and deployment credentials were not used.

## Confirmed problems and repairs

| Severity | Concrete reproduction | Observed failure | Repair |
| --- | --- | --- | --- |
| High | Open a picklist's team notes. Type an edit, wait for its delayed save to start, then type a newer edit before that save completes. | Only the earlier snapshot reached the fixture server. The newer text stayed visible but stopped saving because the earlier acknowledgment cleared the dirty flag. | Saves serialize per list and acknowledge only the submitted snapshot. Newer edits are submitted using the acknowledged server version. |
| High | Edit a team's notes, then select another picklist within the 900 ms autosave delay. Return to the first list. | No update request was made; returning restored the old notes. | Drafts persist per workspace/list before autosave. Switching lists retains the original list's pending save and draft; reloads and route changes recover it. |
| High | In the recorder, load `2026test_qm1`, then change the match input to `2026test_qm2` without reloading its teams. | QM1's six teams and “Continue to calibration” remained available under the QM2 key. This could attribute a recording to the wrong team set. | Editing either setup key invalidates loaded teams and continuation immediately. Responses are checked against both request generation and current context. Malformed or mismatched keys get specific errors. |
| Medium | On the same picklist route, navigate from a slow event to a fast event and back to the original event. | The page hit React error 185 and showed “Picklist failed to load.” The URL/state effects repeatedly restored each other's previous event. | The shared event hook now takes event selection from the URL. Back/Forward, direct links, storage failures, and repeated same-event retry have regression coverage. Picklist event loads ignore superseded responses. |
| Medium | Start saving the offline detector, delay response-body completion, and reject its cache write (for example, quota exhaustion). | The cache promise rejected before its error handler attached. The baseline regression failed with an unhandled rejection and remained waiting on the network stream. The old status also advised Wi-Fi for a storage failure. | Stream consumption and cache persistence attach rejection handlers together; failure cancels the reader. Initial readiness errors are handled. Full storage gets a specific message and an explicit retry action. |

Picklist conflicts now retain the local draft and require an explicit choice between saving local edits and using the shared version. Failed saves expose a retry instead of discarding edits. Pending deletion stops the save timer; editing/deletion controls cannot race an active delete or save. Memory-only drafts remain recoverable through in-app navigation when storage is full, with a visible warning.

## Evidence

Evidence is local under `/tmp/frcmob-independent-stress/`; temporary files may be removed by the OS.

- Earlier-save/newer-edit failure: `picklist-lost-edit-before.json` records the only submitted snapshot and server document; `picklist-lost-edit-before.png` captures the UI.
- List-switch data loss: `picklist-switch-before.json` records zero saves and the restored old note.
- Wrong recorder teams: `recorder-wrong-teams-before.png` and `.txt` show QM2 alongside QM1's teams and continuation.
- Navigation crash: `chrome/quota-route-recovery-390.png` shows the caught React error 185; `chrome/report.json` preserves the sequence. WebKit also reproduced the same navigation failure before repair.
- Unhandled cache failure: `cache-failure-before.log` contains the failing baseline assertion, `PromiseRejectionHandledWarning`, and Vitest's unhandled rejection with the source location.
- Final request traces, assertions and screenshots: `final-chrome-verified/report.json`, `final-webkit-verified/report.json`; screenshots include `picklist-after-390.png`, `recorder-after-1440.png`, and `recorder-cache-recovery-390.png` within those directories.

One earlier WebKit run recorded an intermittent `TypeError: Load failed` without a useful stack. A diagnostic repeat passed. It prompted the controlled cache-failure investigation above; the original exception's exact source is not claimed as proven.

## Actual verification

Concurrent work appeared in `ScoutingPage.tsx`, `scoutingPage.helpers.ts`, scouting recovery files and its suites. Those changes were preserved and excluded from this commit. To verify this work independently, `/tmp/frcmob-independent-stress/verify-owned.mjs` reads the two concurrently edited application source files from `40fb69b` through a Vite load plugin; it does not alter the working tree. The final browser build uses that isolated source combination. Source fingerprints are saved in `owned-source-hashes.json`. Environment-file loading was disabled for Vite and Vitest.

- Isolated frontend unit tests: **487 passed, 91 files**. `unit-owned-final.log`. This includes 17 added regression cases; recorder lifecycle and sync regressions remain passing.
- TypeScript project check completed; isolated production build completed and its actual output was served on loopback port 4179. `build-owned-final.log`.
- Full ESLint check: clean. `lint-final.log`.
- Seven-profile responsive sweep: **316/316**, no page exceptions, crashes, or page-level overflow above its 2 px tolerance. 65 credential-free public GETs returned HTTP 200; private reads and every write were intercepted. `responsive-owned/report.json`. Profiles: 320 px phone, 390 px phone, landscape, tablet, desktop light/dark, and enlarged text. Covers scouting, pit, picklist, events, teams, matches, recorder setup, settings, menus, dialogs, tabs, navigation, and server/offline states. This sweep preceded the final storage-message/retry refinement; that refinement is covered by the final targeted browser checks.
- Existing recovery suite from `40fb69b`: **40/40 Chrome**, **40/40 WebKit**, no page exceptions. `recovery-owned-chrome/report.json`, `recovery-owned-webkit/report.json`. Using the baseline suite avoided including the other agent's concurrent fixture edits.
- Final independent context/save suite: **26/26 Chrome and 26/26 WebKit**, zero page exceptions and zero production writes. Results recorded in `final-chrome-verified/report.json` and `final-webkit-verified/report.json`; covers mobile and desktop slow saves, rapid list switches, reloads, Back navigation, failed writes, conflict choices, same-event retry, out-of-order loads, quota failures, workspace changes, recorder key changes, stale schedules, invalid pasted keys and offline-detector cache failure.
- Workspace recording and offline-sync suites: final logs are `workspace-recording-final.log` and `offline-sync-final.log`; verify interrupted upload retention, explicit conflict retries, pinned workspace access, revocation, recovery export without credentials and a real offline service-worker recorder reload with isolation headers.

Re-run the new suite against a local production preview with `node e2e/stress-context-edits.mjs`; select WebKit with `CONTEXT_BROWSER=webkit`. It intercepts every API request, denies external browser requests, and writes no production data.

A Chrome navigation-recovery fixture initially assumed navigation always preceded the autosave deadline. Under concurrent test load, a legitimate successful autosave made its expected retry button disappear. The fixture now forces a failed write to keep that recovery scenario deterministic. Tests assert the actual request payloads and server fixture state, rather than treating visible text as proof of sharing. A WebKit quota fixture initially recorded zero hits on its injected model-cache failure, so that run was inconclusive. The final fixture uses a fresh browser context, injects failure at the first offline cache write, asserts that the injector was reached, checks the specific storage warning, restores cache writes, and verifies a successful retry in both engines.

## Limits

This is bounded browser verification, not a claim that the app is bug-free. Writes are mocked; backend persistence and real authorization are covered only to the browser boundary in this round. Physical phones remain deferred. No native build or installed-simulator run was performed in this round. Browser viewports do not establish physical keyboard behavior, OS memory pressure, camera quality, thermals, or complete accessibility compliance. The detector was served from existing local assets; no GPU training or cloud work was started.

When storage is full, a memory-only draft cannot survive closing/reloading its document. The UI warns the scout to keep the tab open until changes are shared. Offline picklist conflicts remain whole-document conflicts with explicit operator choice, rather than automatic merging of two scouts' edits.
