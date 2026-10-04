# Native app verification — 2026-10-01

The iOS app now compiles and runs in Xcode's iPhone simulator. Native CSV,
scouting HTML, and recovery JSON exports use app-cache files and the OS share
sheet. Picklist/strategy reports share offline HTML in the installed app; web
Print/PDF behavior is retained. Export errors are visible, share-sheet dismissal
is treated as cancellation, and alliance CSVs use RFC 4180 escaping.

## Environment and scope

- macOS 26.2, Xcode 26.6 (17F113), iOS 26.5 (23F77), iPhone 17 simulator.
- Local simulator signing is ad-hoc; no Apple developer account was used.
- The unsigned simulator build compiled but failed at Keychain startup. Keeping
  normal simulator signing fixed it. `native:build:ios` enforces that default.
- Fixtures contain fake credentials and disposable recording/edit data only.
  HTTP(S) fetches were blocked in the offline fixture; the leave-workspace
  request was mocked. No real workspace was created or changed.
- Physical devices, real camera capture, full-match accuracy/speed, APNs/FCM,
  store signing and distribution are outside these checks.

## Actual iOS simulator results

| Check | Result and evidence |
| --- | --- |
| Normal app build and first launch | Xcode `BUILD SUCCEEDED`; Home rendered with navigation and safe areas. |
| Keychain | Fake value saved/read, restored after cold restart and app update, and removed successfully. |
| Authenticated encryption | Queue edit encrypted/decrypted; modified ciphertext rejected. Encryption key persisted after restart/update. |
| Legacy workspace migration | Fake workspace token moved to Keychain; localStorage copy removed; workspace UI restored. |
| Legacy queue migration | Original blocked edit and conflict status retained in authenticated ciphertext; plaintext queue removed. |
| Sign-out | Real UI leave flow with a mocked server response cleared Keychain access; restart remained signed out. |
| Offline cold start | With WebView HTTP(S) fetches blocked, app opened, showed offline status, and retained the pending recording and encrypted edit. This is simulated request failure, not a radio/network toggle. |
| App update | Installing over the existing app preserved Keychain, encryption key and pending recording. |
| Uninstall/reinstall | Keychain test value survived. WebView localStorage and recordings were removed. The disposable installation was then cleaned. |
| Recovery JSON | OS share sheet opened; actual 441-byte cache file retained the fixture track point and omitted tokens. Save to Files was exercised with an On My iPhone destination. |
| CSV | OS share sheet opened; actual 44-byte cache file contained the expected header/row with CSV comma/quote escaping. |
| HTML report | OS share sheet opened; actual report contained its table and CSS, omitted inputs/scripts, and rendered offline in Chrome without page errors. |
| Recorder screen | Loaded inside `capacitor://localhost`; no isolated-document redirect and no horizontal overflow. |
| Installed detector | Packaged v4 nano loaded and ran using WASM. A cropped black frame took about 777 ms; no detections, as expected. |
| Real video decode/inference | Disposable three-second H.264 clip decoded and sought; frame was nonblank; full-frame WASM inference returned five boxes in about 1,383 ms. No accuracy or identity verdict is implied. |
| Full offline recording flow | Real installed UI: mocked match setup → synthetic four-tap calibration → three-second H.264 import → six synthetic path assignments → nine sampled frames saved locally. No HTTP writes; no identity/position accuracy claim. |
| Imported recording restart | Cold restart retained the unsynced recording and exposed recovery export in My Team. |
| Normal build excludes test hook | Binary string inspection found no `FRCMOB_SIMULATOR_PROBE` or `FRCMOB_SIMULATOR_OFFLINE` markers. |

The simulator hook only compiles with `FRCMOB_SIMULATOR_TEST` **and** a simulator
target. Ordinary debug/release builds exclude it. The explicit probe-build command
is for disposable local verification; rebuild normally afterward.

## Video startup repair

The full-flow check exposed an installed WKWebView import hang after the detector
had loaded: the detached video never emitted its readiness event. The processor
now sets explicit preload, installs listeners before assigning the source, and
calls `load()`. A 15-second startup timeout restores the input with a visible error
instead of leaving the screen stuck. Regression tests cover synchronous media
errors, retry availability, timeout cleanup, and abort cleanup. The installed
full-flow probe passed twice after the repair.

## Other verification

- **461 frontend tests** and lint pass.
- Web and native bundles build; native bundle verifier checks packaged recorder,
  ONNX runtime and both model checksums.
- Chrome native-shell fixture, web offline-conflict recovery, and recording export
  fixtures passed: no page errors or phone-width overflow.
- Android debug APK and unsigned release AAB compiled with debug/release lint.
  APK signature verified. Both model checksums matched the source in APK, AAB and
  normal iOS app. Bridge logging remained disabled.
- Four tests timed out when the simulator and parallel builds were competing for
  resources. A quiet rerun with two workers passed, without changing timeouts or
  skipping tests.

## Reproduce

See `docs/NATIVE_APP.md` for the iOS build and simulator probe commands.
`e2e/ios/probe.mjs` discovers a booted iPhone, or accepts `IOS_SIMULATOR_ID`.
The test-only `video` stage needs `Documents/smoke-video.mp4`; it was generated
locally from the retained project match video and was not committed.

## Remaining beta/release gates

1. Native import/calibration/identity/local-save and restart recovery pass with
   disposable fixtures. Browser fixture tests now also cover workspace join,
   revoked access, interrupted recording uploads, conflict retention, and
   confirmed retries (see `UI_STRESS_TEST_2026-10-01.md`). Real native/server
   integration and event preparation were subsequently exercised with real native
   HTTP, production public reads and a disposable real SQLite backend; see
   `NATIVE_BETA.md`. Production authenticated writes and PostgreSQL concurrency
   remain unverified in this simulator round.
2. Physical camera capture, sustained inference, thermal behavior, and real-device
   update/reinstall testing remain deferred at the user's request.
3. Native push needs APNs/FCM registration and the owner's provider setup. Current
   app copy correctly states that match alerts are unavailable.
4. Store distribution needs developer-account ownership, app ID registration,
   upload signing, privacy/review details, and TestFlight/Play internal testing.

The Android APK is a development build. The AAB is unsigned. The iOS `.app` is a
simulator build. None is a store-ready signed release, and nothing was published.
