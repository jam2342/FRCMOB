# FRCMOB private beta — preparation and verification

October 2 follow-up: PostgreSQL concurrency and revoked-member writes now have
real HTTP regression coverage; see [Beta reliability and ML release audit](BETA_RELIABILITY_2026-10-02.md).
That round also fixes inactive ML candidates leaking into live inference. The
patch is locally verified; the owner approved production rollout on October 2.

## Verified October 1, 2026

- Installed iOS app → production HTTPS: health, schedule and team intel returned 200.
  These were public reads with no credentials or production writes.
- Installed iOS app → disposable real FastAPI backend: workspace creation/join,
  member authentication, recording upload, exact retry, wrong-workspace 409,
  leader removal and revoked-member 401 passed. Persistence contained one new
  session and two points after duplicate uploads. The HTTP bridge was real;
  database was temporary SQLite, not production PostgreSQL.
- Real browser UI → that backend: double-click join issued one request; a committed
  upload with a deliberately lost response remained pending; retry did not
  duplicate data; a real workspace mismatch 409 remained pending without automatic
  replay; confirmed retry synced; real member removal cleared app access and
  preserved the recording. No production workspace was created.
- Installed iOS app prepared Archimedes: **18/18 steps**, **76 team pages**,
  **111 public GET/HEAD requests**. Cold restart with HTTP(S) fetches blocked
  verified required cache entries and rendered actual cached schedule rows.
- Fixed native event persistence: native response bodies use IndexedDB rather than
  WKWebView's unavailable/unreliable Cache Storage or the small localStorage quota.
  Expiration and entry bounds remain enforced; workspace-scoped keys remain distinct.
  Offline preparation excludes unrelated background events from its audit.
- Native push delivery remains unavailable; no APNs/FCM delivery is claimed.

## Validation and retained artifacts

Frontend **466/466 tests** and lint pass. Targeted backend workspace/security/sync
coverage passes **57/57 tests**. The backend run used no dotenv or production
credentials; warnings include the existing short synthetic JWT keys in test fixtures.
Production web/native bundles, Android debug build/lint, Android release bundle/lint,
normal iOS simulator build and unsigned iOS device archive pass. Both ONNX model
hashes match source in the Android and iOS packages, and iOS probe markers are absent
from both normal executables. Reclaimed **590.5 MiB** of regenerated build
intermediates and disposable Python environments while retaining the packages and
device archive. The installed normal simulator app was launched and
its screenshot inspected.

Local evidence logs: `/tmp/frcmob-beta-native-live.log`,
`/tmp/frcmob-beta-real-workspace.log`, `/tmp/frcmob-beta-native-local.log`,
`/tmp/frcmob-beta-event-prepare.log`, `/tmp/frcmob-beta-event-offline.log`,
`/tmp/frcmob-beta-tests.log`, `/tmp/frcmob-beta-backend-tests.log`,
`/tmp/frcmob-beta-native-shell.log` and `/tmp/frcmob-beta-web-offline.log`.
No signed owner release, native notification delivery, physical device performance,
production authenticated write, PostgreSQL concurrency, store upload or beta
invitation is claimed. The earlier UI stress report remains the visual baseline;
these changes concern persistence, integration and release tooling.

## Release commands (local only)

From `ScoutingApp/`:

```sh
npm run native:beta:doctor
npm run native:archive:ios
npm run native:bundle:android
```

The readiness check deliberately exits nonzero while account/provider gates remain.
It reads metadata and file presence only, not signing/provider credentials.
The iOS device archive is `ios/archives/FRCMOB.xcarchive`: Release/arm64, unsigned,
with no simulator probe. It must be rebuilt with the owner's registered developer
team and distribution provisioning, then exported through Xcode for TestFlight.
The simulator `.app` is not a TestFlight artifact.

Android release signing now accepts four owner-supplied environment variables:
`FRCMOB_UPLOAD_KEYSTORE`, `FRCMOB_UPLOAD_STORE_PASSWORD`,
`FRCMOB_UPLOAD_KEY_ALIAS`, `FRCMOB_UPLOAD_KEY_PASSWORD`.
Supply them through a private local environment/secret manager, never a command
argument, committed file, chat, or Vite variable. Then use
`npm run native:bundle:android:signed`. Missing/partial configuration fails closed.
With all variables absent the ordinary release command still builds an unsigned
AAB. No upload key was created, read, or selected by this work. The signed command
was verified to reject absent credentials; an owner-signed build remains untested.

Before uploading, verify the AAB signature and upload-certificate fingerprint
against the owner's Play Console configuration. Do not substitute the debug key.
Publishing, uploading, adding testers, and invitations require owner approval.

## Owner setup needed

1. Register `app.frcmob.scouting` under the Apple Developer and Google Play accounts;
   confirm whether this is the final identifier before distributing it.
2. Configure Apple signing/team and the Android upload key. Record the public
   certificate fingerprints and keep private signing files outside git.
3. Register the Android app in the owner's Firebase project; provide its local
   `google-services.json`. Enable Apple's Push Notifications capability and APNs
   provider setup for the same app ID. Backend provider credentials belong only on
   the server. Client registration, native subscription persistence and APNs/FCM
   sending still need implementation and verification after the provider setup;
   the existing Web Push sender does not deliver native pushes.
4. Supply support contact, developer legal identity, store listing details and
   final privacy/data-safety declarations. Verify every declaration against the
   shipped version before submission.

Official references: [Apple beta distribution](https://developer.apple.com/documentation/xcode/distributing-your-app-for-beta-testing-and-releases),
[Android upload signing](https://developer.android.com/studio/publish/app-signing),
[Capacitor native push setup](https://capacitorjs.com/docs/apis/push-notifications).

## Beta tester workflow

Use a separate beta workspace and synthetic notes first. One leader and two scouts
join it on different installations; a second workspace checks that private data
never crosses teams. Prepare one event on Wi-Fi, then use it with API connectivity
blocked. Enter pit/match notes, keep a pending recording through restart, reconnect
and confirm server receipt. Disconnect during upload and confirm the same ID is
retried. Remove one scout and confirm private access stops without deleting their
saved recording. Export pending work before uninstalling.

When physical-device testing resumes, film a complete match and record inference
speed, thermal behavior, memory, video duration, retained frames, calibration
quality and tap-ID effort. This is required evidence before public recorder claims.
Native push testing must cover permission denial, registration rotation, expired
provider tokens, foreground/background/killed delivery, correct event timing,
workspace revocation and notification deep links.

Track device/OS/app build, steps, expected/actual result and screenshots for each
failure. Do not put workspace tokens, join codes, private notes or personal photos
in reports. A small beta can start without native match alerts if that limitation
is clearly disclosed and owner signing is finished. No invitations were sent.

## Suggested listing copy (draft)

**FRCMOB — FRC scouting and match analysis**

Follow events, compare teams, and coordinate scouting with your team. Save event
information before heading to a venue, collect pit and match notes, and build your
picklist. Import match video for on-device robot tracking, with field calibration
and manual team identification. Review results before using them for strategy.

FRCMOB is an independent app and is not affiliated with FIRST. Native match alerts
are unavailable in this build. Video tracking remains experimental and needs an
operator's review. Existing browser/PWA data does not automatically move into the
native app.

## Reproduce the disposable backend checks

Use Python 3.12 and the versions in `backend/requirements.txt` for FastAPI, uvicorn,
SQLAlchemy, Pydantic/settings, PyJWT, httpx, redis, requests and psycopg. The harness
does not need torch, detector training packages, Redis, or production credentials.
Launch from the repo root with a clean environment (substitute your venv executable):

```sh
env -i PATH=/usr/bin:/bin PYTHONPATH="$PWD/backend" /path/to/venv/bin/python backend/tests/native_beta_server.py
```

It binds **127.0.0.1:4182 only**, uses a temporary DB, disables dotenv before
constructing settings, and deletes the DB on orderly shutdown. Its synthetic
local signing secret is not a production credential. Do not expose this server.
Serve the web build at 4179, then run:

```sh
cd ScoutingApp
WORKSPACE_REAL_API=http://127.0.0.1:4182 npm run validate:workspace-recording
```

The fixture checks the disposable-server marker and refuses any other real API URL.
Restart the server before repeating this test so its DB-count checks start fresh.
For installed iOS checks, build/install the opt-in simulator probe, then:

```sh
node e2e/ios/probe.mjs live-read
node e2e/ios/probe.mjs event-prepare
IOS_PROBE_OFFLINE=1 node e2e/ios/probe.mjs event-offline
node e2e/ios/probe.mjs local-server
npm run native:build:ios # restore normal build, then install it
```

Use a signed-out disposable installation. Event preparation allows only GET/HEAD
requests; local-server writes go solely to loopback. Public event packs are retained
for offline checks. Simulator test hooks remain excluded from normal builds.
