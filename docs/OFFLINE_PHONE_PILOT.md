# Offline phone pilot

Use a real phone for this check. Browser emulation does not prove that a Home Screen app survives an iOS restart, storage pressure, or a venue with no signal. Run it once before an event and again after any major recorder or offline-cache change.

Automated baseline (2026-09-30): Chrome opened saved event pages and the isolated recorder offline. Playwright WebKit saved the event pack and reopened Events offline. Its `setOffline(true)` switch failed navigation with a browser-level error that matches [Playwright issue #42775](https://github.com/microsoft/playwright/issues/42775). With the test server stopped instead, WebKit opened the cached, isolated recorder successfully. The physical iPhone check remains necessary.

## Record before starting

- Device and OS version:
- Browser used to install:
- Event key:
- Team workspace name (never record the join code or access token):
- Available device storage:
- Start time:

## iPhone walkthrough

1. In Safari, open https://scouting-app-iryg.vercel.app, tap Share → Add to Home Screen, then launch FRCMOB from its new icon. Work in that Home Screen app for the rest of the check.
2. Join your existing team workspace and select the event in Events. On Wi-Fi, open My Team → **Ready for no signal** and tap **Get ready for offline**. Wait for **Match recorder: Saved on this phone** and **Event data: Verified**. Note any **Needs retry** item and tap **Update saved data** until it clears or record the failure.
3. Turn on Airplane Mode and turn Wi-Fi off. Fully close the Home Screen app and reopen it from the icon. Confirm My Team, Events, one Team Center page, Strategy, and Match recorder open with saved information. A live-only value may be stale; it should be labeled as saved rather than presented as current.
4. While offline, save one clearly marked test scouting entry in your workspace. In My Team → **Saved on this phone**, confirm the scouting-change count increased. Do not clear website data or uninstall the app while anything is waiting.
5. If you have a short, consented practice recording, run a brief recorder session and save it. Confirm the recording count increased. Keep the video local until you are ready to sync.
6. Reconnect to Wi-Fi, open My Team, and tap **Sync now**. Both waiting counts should reach zero, **Last confirmed sync** should show a recent time, and no **Needs attention** message should remain. Verify the test entry from a second device or a fresh browser session in the same workspace.
7. Leave the Home Screen app closed for a day. Turn Airplane Mode back on before reopening it from the icon. Check that the event pack and recorder still show saved/verified. Repeat the sync check if you made new offline changes.

## Record the result

| Check | Pass / fail | What happened, including the exact on-screen message |
| --- | --- | --- |
| Home Screen install and event preparation | | |
| Cold reopen in Airplane Mode | | |
| Events, Team Center, and Strategy offline | | |
| Recorder opens offline | | |
| Scouting change queued | | |
| Recording queued | | |
| Both queues sync and receipt appears | | |
| Next-day offline reopen | | |

If a check fails, record the step, time, device/OS version, and a screenshot. Do not share workspace join codes, access tokens, or personal video. The next engineering task is to reproduce and fix the first failed step before expanding the pilot to more scouts.
