# FRCMOB native app

The React frontend now has Capacitor 8.5.2 projects for iOS and Android. App name:
**FRCMOB**. Initial bundle/application ID: `app.frcmob.scouting`. This ID must be
registered under the owner's developer accounts before store submission.

The app installs its interface, lazy-loaded pages, both v4 detector models, ONNX
runtime, fonts, and field-tracker code. It does not load the website as its shell.
The website remains a separate `dist/` build; native assets go to `dist-native/`.
Event data still needs to be downloaded before arriving at a venue with no signal.
The installed app has its own storage; existing website/PWA drafts and credentials
do not automatically transfer. Sync or export browser work before switching.

## Build

Use Node 22 or newer, JDK 21, and Android SDK platform/build-tools 36. Full Xcode
with an iOS SDK and simulator is needed for an iOS build; Command Line Tools alone
cannot compile the iOS application.

From `ScoutingApp/`:

```sh
npm ci
npm run native:doctor
npm run native:sync
npm run native:build:android
npm run native:build:ios # local simulator build, ad-hoc signed
npm run native:bundle:android # unsigned release AAB + release lint
```

Native version is **0.1.0 (build 2)** on both platforms. Branding uses the existing
`public/Heading.png`, with generated icons and splash assets committed. On macOS,
`npm run native:branding` regenerates them without any external service.

The debug APK is `android/app/build/outputs/apk/debug/app-debug.apk`. It is a local
development build, not a signed Play Store release. It includes the current web
bundle and models. `native:build:android` also runs Android lint.

The unsigned release bundle is `android/app/build/outputs/bundle/release/app-release.aab`.
It has no release signing configuration and cannot be uploaded to Play as-is.
The app owner must set up upload signing and store ownership; do not use the debug
key for store distribution. Native bridge argument/response logging is disabled
in every build, since headers and secure-storage calls can carry credentials.

Open the IDE projects with `npm run native:android` or `npm run native:ios`.
For a simulator build once Xcode is installed:

```sh
npm run native:sync
xcodebuild -project ios/App/App.xcodeproj -scheme App \
  -destination 'generic/platform=iOS Simulator' \
  -derivedDataPath ios/DerivedData build
```

Keep normal simulator ad-hoc signing enabled: disabling signing compiles the app
but makes Keychain access fail at startup. Simulator signing needs no paid Apple
developer account.

Android build scripts use `ANDROID_HOME`/`ANDROID_SDK_ROOT` and `JAVA_HOME` when
provided. On this Mac they also find the SDK at
`~/Library/Caches/frcmob-native/android-sdk` and Homebrew's keg-only JDK 21.
No shell configuration or existing Java installation needs to change.

## Runtime behavior

- Native HTTP uses the public `https://scouting-app-iryg.vercel.app/api` service.
  Live rooms use `wss://141-148-171-128.sslip.io`. Native HTTP fetch patching avoids
  requiring WebView origins in the backend's browser CORS list. Optional public
  overrides are `VITE_NATIVE_API_URL` and `VITE_NATIVE_WS_URL`; insecure/relative
  endpoints are rejected. Website endpoint overrides do not affect the native app.
- The native app does not register a service worker or redirect to `record.html`.
  Its packaged files supply offline pages and recorder assets. Readiness checks
  confirm the model/runtime files are served by the local asset handler.
- The detector still probes WebGPU and falls back to WASM. Without cross-origin
  isolation it uses one WASM thread. An Android 16 / WebView 133 emulator loaded the nano model and executed one
  black test frame with WASM. This is a runtime smoke check, not an accuracy or
  full-match speed benchmark. Camera capture and video decoding still need validation.
- Android Back follows navigation history, returns an initial deep route to Home,
  and minimizes the app at Home. Foregrounding rechecks connectivity and retries
  eligible pending work. No claim is made that inference or sync runs in background.
- `frcmob://open#/my-team` and other local hash routes open inside the app. External
  website links open through the system browser UI. Universal/App Links are not set
  up; custom scheme links are only a convenience, not authentication.
- Recovery exports use native app-cache files and the OS share sheet. Exports do not
  include authorization fields, and sharing does not delete the pending originals.
  CSV exports, scouting HTML reports, and recovery JSON use the same native share-sheet path. Picklist and strategy Print/PDF actions become **Save report** in the app and share a self-contained HTML report; web builds retain Print/PDF.
- Android camera access and iOS camera-purpose text are configured. No microphone
  permission is requested for silent match recording. Android app backup is disabled
  to avoid restoring private workspace credentials to another installation.

## Verification

```sh
npm test
npm run lint
npm run build
npm run build:native
npm run preview -- --port 4179 --strictPort
# In another terminal:
node e2e/validate-native-shell.mjs
node e2e/validate-offline-sync.mjs
node e2e/validate-recording-sync.mjs
```

The native-shell check runs the actual built UI with a **mocked Capacitor bridge**
and local API fixtures in installed Chrome. It checks native endpoint selection,
share exports, external links, app links, Back, no recorder document hop, no service
worker, and phone overflow. It is not an emulator or a native performance test.

The bundle verifier checks that both installed models match their source checksums,
and that recorder/runtime files are present. Generated bundles, signing material,
SDK paths, build output, and keystores are ignored by git.

## Verified foundation (2026-10-01)

- 447 frontend tests, lint, web/native bundles, Android compilation and Android lint.
- Debug APK signature and both detector model checksums verified from the APK itself.
- Real Android 16 / Chromium WebView 133 emulator: app rendered, native HTTP health
  returned 200, local recorder assets were ready, and the nano detector ran one frame.
- Real native recovery export: Android share sheet opened; its cache file retained
  the fixture points and omitted authorization data. No share destination was selected.
- Offline cold start: Wi-Fi/mobile data disabled, backend unreachable, the app opened,
  a pending recording survived restart, and recorder assets remained ready. WebView's
  `navigator.onLine` stayed true; backend health correctly showed the offline state.
- iOS plists, resource registration, and Swift package parsed. This was the initial pre-Xcode check; full compilation and simulator checks now pass (see the iOS verification section below). No physical phones were tested.

## Secure storage (2026-10-01)

Native workspace sessions use a local Capacitor plugin: Android AES-256-GCM with a
non-exportable Android Keystore key and private encrypted preferences; iOS uses
Keychain generic passwords (`AfterFirstUnlockThisDeviceOnly`, no iCloud sync).
Queued native mutations are authenticated ciphertext in IndexedDB, including
headers and the original edit, with a device-bound key. Recording points remain
in IndexedDB without credentials. Browser storage behavior stays unchanged.
Admin and room access remains tab-scoped in memory in native builds; it is not
saved to WebView sessionStorage and is reacquired after a cold start.

Startup awaits workspace and queue migration before rendering the app or starting
sync. Legacy plaintext is removed only after secure writes finish. Interrupted
queue migration uses the newer IndexedDB row when a legacy duplicate remains.
Failures retain the originals and show a retry screen; native persistence never
falls back to plaintext. Sign-out clears access immediately and serializes vault
removal behind any in-flight save; a non-secret pending-removal marker prevents
an unsuccessful removal from restoring access at the next startup.

The vault protects data at rest, not a compromised running app. Removing the OS
key makes encrypted queued edits unreadable; never clear app data while scouting
work is pending. The iOS simulator confirmed that Keychain entries survive uninstall while
WebView data and recordings are removed. Sign out and sync/export pending work
before uninstalling; app updates preserve both access and saved work.

Verified: **454 frontend tests**; Android debug/release compile and lint; actual
Android emulator migration with fake credentials and a blocked queued edit,
offline cold restart, app update, sign-out, and rejection of tampered ciphertext.
Server writes in the sign-out smoke check were mocked; no workspace was created
or changed on the live server. Native-shell and web offline/recovery fixtures pass. The Native Android CI workflow
compiles the native plugin and runs Android lint against a minimal fixture shell;
it does not package detector weights, which are deliberately excluded from git.
The initial iOS check only parsed Swift/project sources. Full iOS compilation,
Keychain migration, sign-out, cold restart, and simulator video inference now pass.

## Before a beta or store release

1. Expand native full-flow checks: live workspace join/revocation, complete video
   import/calibration/identity, event preparation, and interrupted server sync. iOS
   local persistence, offline recovery, migration, sign-out, exports, video decoding
   and single-frame inference now pass; these are not a full-match accuracy check.
2. Verify the recorder on native devices when physical testing resumes. Keep the
   existing detector and conservative stitching until measurements justify changes.
3. Preserve the verified secure-storage behavior on both platforms. Physical
   device upgrade/reinstall checks remain part of beta testing.
4. Finish keyboard polish and native push registration. Web Push is not a substitute for APNs/FCM setup; native Match Alerts show an explicit unavailable state.
5. Finalize app ID ownership, signing, privacy disclosures and review notes; distribute
   through TestFlight and Play internal testing before submitting to the stores.

No store enrollment, signing credentials, paid services, backend deployment, or
store publication is part of this foundation. Do not place server/API secrets in
Capacitor config or Vite variables: the entire client bundle is public to its owner.

The CLI's `xcode` dependency uses a scoped `uuid` 11.1.1 override to avoid adding its
older vulnerable UUID version. Project parsing and resource registration were checked.

References: [Capacitor setup](https://capacitorjs.com/docs/getting-started),
[native HTTP](https://capacitorjs.com/docs/apis/http),
[app lifecycle](https://capacitorjs.com/docs/apis/app),
[filesystem privacy manifest](https://capacitorjs.com/docs/apis/filesystem).

## iOS simulator first launch (2026-10-01)

Xcode 26.6 (17F113), iOS 26.5 (23F77), iPhone 17 simulator: native bundle
checks passed, Swift/package compilation succeeded, and the installed app rendered
Home with its navigation and safe-area layout. Workspace bootstrap completed using
Keychain. An unsigned build failed at secure-storage bootstrap; rebuilding with
normal simulator ad-hoc signing fixed startup. Subsequent checks verified Keychain
migration, encrypted queued edits, tamper rejection, sign-out, restart/update
persistence, native CSV/JSON exports, offline recovery, and video decode/inference. The unused iOS 26.2 runtime was removed
to reclaim storage; about 11 GiB remained free after the build.


## Repeatable iOS simulator checks

Use an iPhone simulator with a **disposable local app installation**. The fixtures
use fake access and block HTTP(S) requests in the WebView; the sign-out endpoint
is mocked. The network fixture verifies app behavior when requests fail, not a
physical radio/network setting. No real workspace or server mutation is needed.

```sh
npm run native:build:ios:probe
# Boot an iPhone simulator in Xcode/Simulator, then install the probe app:
xcrun simctl install booted ios/DerivedData/Build/Products/Debug-iphonesimulator/App.app
node e2e/ios/probe.mjs seed
node e2e/ios/probe.mjs verify
IOS_PROBE_OFFLINE=1 node e2e/ios/probe.mjs migration-seed
IOS_PROBE_OFFLINE=1 node e2e/ios/probe.mjs migration-verify
IOS_PROBE_OFFLINE=1 node e2e/ios/probe.mjs signout
IOS_PROBE_OFFLINE=1 node e2e/ios/probe.mjs signedout-verify
IOS_PROBE_OFFLINE=1 node e2e/ios/probe.mjs inference
IOS_PROBE_OFFLINE=1 node e2e/ios/probe.mjs recorder
# With the disposable Documents/smoke-video.mp4 fixture installed:
IOS_PROBE_OFFLINE=1 node e2e/ios/probe.mjs recording-flow
IOS_PROBE_OFFLINE=1 node e2e/ios/probe.mjs recording-restart
# Optional interactive checks: share and csv open the OS share sheet.
node e2e/ios/probe.mjs share
node e2e/ios/probe.mjs csv
node e2e/ios/probe.mjs clean
# Always restore the normal app after testing:
npm run native:build:ios
```

`IOS_SIMULATOR_ID` selects a specific simulator; otherwise the probe uses a booted
iPhone. The hook requires the explicit `FRCMOB_SIMULATOR_TEST` compile condition
and simulator target; it is absent from normal debug/release builds. Probe results
are written inside the simulator app's Documents folder as `native-smoke.json`.

The `recording-flow` probe drives match setup, synthetic four-tap calibration,
video import, synthetic identities and local save. `recording-restart` checks that
recording survives a cold restart. These are flow checks, not accuracy benchmarks.
Imported video explicitly starts media loading and reports a retryable error if
readiness does not arrive within 15 seconds.

The `video` and `recording-flow` probes expect a disposable H.264 `Documents/smoke-video.mp4` fixture
in the simulator app. The `reinstall` probe is for a separate destructive test of
that disposable app only: after `seed`, uninstall/reinstall it and run `reinstall`
to confirm Keychain retention and removal of WebView/recording data. Never run
that test on a device containing scouting work.

Detailed evidence and remaining release gates:
[`NATIVE_IOS_VERIFICATION_2026-10-01.md`](NATIVE_IOS_VERIFICATION_2026-10-01.md).

## Private beta preparation

See [NATIVE_BETA.md](NATIVE_BETA.md) for verified native/live public reads, real
loopback backend writes, event-pack restart checks, unsigned iOS device archives,
opt-in Android upload signing, provider/account gates and the tester workflow.
