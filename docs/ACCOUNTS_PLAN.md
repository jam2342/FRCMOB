# Accounts plan

Status: planned (2026-10-05). Design drafted with a Codex `gpt-6.1-sol` review of the
identity code, then adjusted and decided with the owner.

## What accounts are for

Today a membership is effectively a device: a signed workspace token names a
`member_key`, and a scout who loses their phone needs a leader to remove the old
device before rejoining under their name. Accounts add, around the existing
membership model:

- **Recovery:** sign in on a new phone and get the same membership back (name,
  assignments, role, recordings).
- **Multiple devices:** phone and laptop share one membership.
- **Device control:** revoke a lost phone without removing the person.
- **Privacy:** export your data and delete your account.

Unchanged:

- Public pages (events, ratings, predictions, team pages) need no login.
- Join codes still admit people to a team; accounts are optional at first.
- A verified email never proves team membership or grants leadership.
- Removal still blocks access immediately; offline scouting keeps working.
- Unsynced work saved only on a lost phone can't be recovered: "Your team access is
  recoverable. Unsynced work stays on the device where you saved it."

## Decisions

| Question | Decision |
|---|---|
| Sign-in method | 6-digit email code. No passwords. Passkeys later. Google and Apple later and together (adding Google on iOS requires Sign in with Apple, guideline 4.8). |
| Who verifies codes | **Our FastAPI backend**, codes hashed in Redis; **Resend** sends the email. No auth vendor: FRCMOB keeps its own sessions anyway, and a hosted free tier that pauses when idle is a risk on event mornings. |
| Email domain | Not bought yet. Build and test against a test inbox; email sign-in stays off (feature flag) until a domain with SPF/DKIM/DMARC exists. |
| Age | Accounts for **13 and over**, checked with a neutral eligibility screen before any email is collected; only the age band is kept. Under-13 users keep public browsing and code-based scouting. Under-13 accounts would need verified parental consent, a separate project with a legal review. |

## Data model (additive)

- `users`: id (UUID), status, created_at, privacy-policy version, age band.
- `user_identities`: user_id, kind (`email` now; `apple`/`google` later), subject,
  verified email; unique (kind, subject). Never merge users because emails or names match.
- `account_sessions`: per device: id, user_id, label/platform, created/last-used/expiry/revoked.
- `account_refresh_tokens`: hash only, session id, rotation family, used/revoked times.
- `team_workspace_members.user_id`: nullable link; **keep `member_key`** (recording
  de-duplication hashes `workspace_id:member_key`, `routes_tracks.py`).
- `workspace_device_sessions`: member id, optional account session id, expiry/revoked.
- One active membership per account per workspace (partial unique index).

## Credentials

| Credential | Lifetime | Notes |
|---|---|---|
| Account access token | 15 min | `X-Account-Access` header; never grants admin. |
| Account refresh token | 90-day idle, 1-year absolute | Browser: `HttpOnly; Secure; SameSite=Lax` host-only cookie on `/api/auth` (CSRF token + Origin check on cookie mutations). Native: Keychain/Keystore. Rotated, reuse detected. |
| Workspace device token | 7 days, renewed while online | Checked against member and device session on every request. |
| Room token | unchanged | |
| Existing guest workspace token | unchanged during transition | |

Email codes: 10-minute single use, 60 s resend cooldown, 5 attempts per code, generic
responses (no account enumeration), Redis limits per email/challenge plus a generous IP
ceiling (teams share venue wifi; real client IP through the proxies).

## Linking and recovery

1. My Team gets "Protect this membership with an account".
2. Linking needs **both** a valid workspace credential **and** a fresh email code.
3. Under the workspace lock, attach `user_id` to that exact membership; reject conflicts.
4. Issue a device-bound credential; other legacy devices of that member sign in again.
5. On a new device, signing in lists linked memberships; picking one issues a new
   device credential for the **same** member, no join code or new name.
6. A removed membership is never silently restored. A legacy member who already lost
   their phone keeps the leader-assisted path. The locked "Legacy data" workspace stays
   unclaimable.

Sign out (this device), Leave workspace, and Remove member become three distinct actions.

## Offline queue and recordings

- Queued writes store their workspace id and member id; credentials are resolved at
  replay time **for that same member only**, never "whoever is signed in now".
- Distinguish expired, revoked, removed and conflict; retry an expiry once after renewal,
  otherwise pause and keep the write for recovery export.
- Legacy queue migration is atomic and keeps originals on failure.
- On-device recording sync gets the same member binding.
- An expired token stops uploads but keeps drafts and offline use.

## Privacy and stores

Before public sign-up: data export (no tokens, nothing from other workspaces), account
deletion in-app (Apple) and via a web page (Google Play), deletion that removes the user's
identifiers and authored content while keeping teammates' work, updated privacy policy and
terms (the current policy says "No accounts, no personal profiles"), App Store privacy
labels and Play Data Safety.

Also fix on the way: workspace tokens in WebSocket URLs become short-lived socket tickets
(and are redacted from logs); auth endpoints get `Cache-Control: no-store` and are excluded
from service-worker and offline caches; structured 401 reasons replace "401 means revoked".

## Phases (one PR each)

1. **Schema**: new tables and nullable link, flag off. Upgrade tested on a disposable copy
   of the production schema; existing workspace tests unchanged.
2. **Queue and storage foundation**: member-bound queue and recording sync, renewal hook,
   account vault on native, paused-auth state. No sign-up yet.
3. **Email code sign-in**: code start/verify (Resend adapter with a test-inbox mode),
   sessions, refresh, logout, 13+ screen, account settings. Flag stays off in production.
4. **Link and recover**: linking, account workspace list, pick an existing membership on a
   new device. Tests: concurrent claims, name takeover denied, removed member stays removed,
   recording de-duplication unchanged.
5. **Devices and rooms**: device list and revoke, socket tickets, push bound to device
   sessions, two-worker revocation of HTTP and sockets.
6. **Export, deletion, disclosures**: jobs, external deletion page, policy and store text.
7. **Pilot**: one consenting team on PWA, iPhone and Android: new-phone recovery,
   airplane-mode start, expiry while offline, reconnect sync, removal.

The test that decides it: a scout links their membership, saves offline, restarts the
phone, reconnects after the token expired, and every edit syncs once; a replacement phone
restores the same membership; revoking the old phone stops its writes, sockets and private
alerts without removing the scout.

## Owner-only steps

- Buy a domain and add DNS records for Resend (needed before email sign-in goes live).
- Create the Resend account; keep its API key out of git (VM `.env` only).
- Approve the privacy policy, terms, and retention/deletion rules (a qualified privacy
  review before public launch is recommended).
- Store listings: privacy labels, Data Safety, audience declarations.
