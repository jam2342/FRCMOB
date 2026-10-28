# UI mistake and recovery stress test — 2026-10-01

Local testing on `feat/native-ios-verification`. No production writes, publishing, or physical-phone testing.

## Reproduced and fixed

- **Pit notes disappeared:** navigation, reloads, switching robots, and failed saves discarded unfinished notes. Drafts now recover separately per workspace, event, and robot, and reopen the last robot. A completed save cannot clear newer edits made while it was pending.
- **Disconnected private reads:** a failed load could leave no way to recover a local draft. Existing drafts now remain editable; sharing/photos are disabled until server notes successfully reload, avoiding accidental overwrites. Loading the same event again retries.
- **Full storage:** persistence failures are visible rather than reported as successful saves. Notes remain in memory through route changes; leaving warns. Closing the app cannot guarantee memory-only recovery.
- **Stale event responses:** switching from a slow event to a faster one could replace current data with old results. Cancelled loads are ignored.
- **Invalid workspace inputs:** `-254` silently became team 254. Team numbers now require positive digits, blank names are rejected, and malformed join codes produce immediate instructions. Names are trimmed and valid human-friendly Crockford codes are normalized.
- **Invalid pit inputs and photos:** negative numeric values receive specific messages; non-image files and corrupt images produce useful errors without uploads. Errors are announced and brought into view; toggle selections expose pressed state.
- **Event Center error timing:** the broad sweep found two uncaught errors when parallel requests failed before the schedule finished. Rejection handlers now attach to all parallel requests immediately, while the schedule can still render first.

## Verification

The initial targeted baseline failed 10 of 12 checks. The first broad sweep checked 316 states and exposed the two Event Center errors above; those failures were investigated and repaired rather than omitted.

Final results are recorded below. The repeatable suites are `npm run validate:user-errors` and `npm run validate:ui-stress` against a local production preview on port 4179. `USER_BROWSER=webkit` selects WebKit for the recovery suite.

The recovery suite intercepts every API request and uses disposable fixture workspaces. It exercises mobile and desktop invalid inputs, lost connections, failed saves, edit-during-save, wrong/corrupt files, draft scope, slow response races, retry, and injected storage quota errors. The broad suite uses credential-free public GET data, intercepts private requests and every write, and checks seven profiles: 320 px phone, 390 px phone, landscape, tablet, desktop light/dark, and enlarged text. It covers routes, tabs, menus, dialogs, rapid navigation, and server/offline error states.

This is bounded browser/simulator verification, not proof that every UI interaction or accessibility requirement is covered. Storage quota errors and connection failures are injected; physical devices remain deferred. Screenshots/logs under `/tmp` are local evidence and may be removed by the OS.

## Confirmed final checks

- Full seven-profile layout and interaction sweep: **316/316**, zero page crashes/exceptions or page-level overflow above the 2 px tolerance; 65 public GET responses, zero production writes. `/tmp/frcmob-user-route-stress-final/report.json`.

- Chrome recovery suite: **40/40**, zero page exceptions; `/tmp/frcmob-user-errors-chrome-final-verified/report.json`.
- WebKit recovery suite: **40/40**, zero page exceptions; `/tmp/frcmob-user-errors-webkit-final/report.json`.
- Repaired smallest-phone broad sweep: **46/46**; `/tmp/frcmob-user-route-stress-repaired/report.json`.
- Frontend: **470 tests / 87 files passed**, lint clean, production build completed.
- Workspace recording and offline sync regression suites passed, including conflicts, interrupted uploads, retries, revocation, and credential-free recovery exports.
- iOS simulator build and unsigned device archive completed; Android debug APK/lint and unsigned release AAB completed. Actual APK/AAB ZIP integrity checked, detector hashes matched across all four packages, simulator signature verified, and simulator-only probe markers were absent from iOS binaries. Rebuilt normal app installed/launched on iPhone 17 simulator; screenshot visually reviewed.
- Removed **535,469,093 bytes** of regenerable project build intermediates, retaining runnable packages and archive.

A repeat under simultaneous builds exposed a timing assumption in the old slow-event fixture. The final fixture waits for the slow request to start, switches routes inside the app, and verifies after the old response completes; both engines pass this stronger check.
