# Security fix results — 2026-10-17

Update: the combined candidate on current main and subsequent read-only production observations are recorded in [release readiness](SECURITY_RELEASE_2026-10-17.md). The results below describe the original isolated security checkout.

Local fixes for all six findings in [the audit](SECURITY_AUDIT_2026-10-17.md) are implemented in `<local>/FRCMOB-security-20261017`. The user approved these security, dependency and migration changes, then asked to leave Git alone. The original working checkout and its concurrent cleanup edits were preserved. No merge, commit, push, deployment, production test or real credential access was performed.

## Changes

| Finding | Local prevention |
| --- | --- |
| F1: private recording cache classification | Any workspace/room credential disables shared caching. Any `include_workspace` parameter is private regardless of boolean spelling or duplicate values. |
| F2: invented events bypassing photo limits | Added cumulative workspace/site byte and photo-count quotas and a free-disk reserve. Validate actual bounded JPEG/PNG/WebP images, including pixel decode; reject fake headers, truncated images and animation. A PostgreSQL transaction advisory lock holds across the global check, file write and commit. Failed commits remove their uploaded file. Deleting photo-only entries cleans up empty placeholder rows/directories, bounding upload/delete churn. Notes remain writable when photo storage is unavailable. |
| F3: anonymous push persistence and fan-out | Validate actual P-256 keys and auth bytes; cap retained subscriptions and new hourly registrations; expire stale records; fail closed on shared-budget failure in production; preserve existing endpoint updates. Unknown events cannot register event alerts, and the scheduler only contacts upstream for bounded local events with relevant unstarted matches. |
| F4: recycled names gaining report ownership | Migration `20261017_0027` adds nullable immutable member authorship. Authorize replacements by member ID, preserve same-author retries/corrections, and reject ambiguous legacy/cross-member replacements with 409. Rename and coverage attribution use immutable authorship. |
| F5: stale socket identity | Room tokens bind the immutable member ID. HTTP room writes, sockets and room-token recording sync enforce that binding. Sockets recheck member/name/token expiry on messages and idle intervals. |
| F6: cheap automated signup exhaustion | Public workspace creation requires server-verified Cloudflare Turnstile in production. Validation checks one-use provider success, exact action and allowed hostname before consuming signup capacity. Missing configuration/provider outages fail closed for creation. Existing-workspace joins remain available. |

The frontend gets a public signup policy, renders the challenge only where required, disables creation until a token exists, and clears the single-use token after every attempted submission. Expiry/errors clear the token. Native clients offer browser creation and can then join normally. CSP permits the exact Turnstile provider origin while retaining existing script hashes, object blocking and framing protections. Privacy copy names the new security provider and subscription expiry.

`source-map-js` was updated from 1.2.1 to 1.2.2 in the frontend lockfile and local dependencies. Pillow 12.3.0, already used by the training/test environment, is now an explicit backend runtime dependency for image validation.

## Verification

- Full backend unit suite: **632 passed, 18 skipped, 2 subtests passed**. External connections were blocked during this run; fixtures used disposable data. Skips include optional integration/fixture gates and are not counted as verified.
- Full frontend suite on Node 26: **839 passed, 187 test files**.
- Frontend production build, ESLint and backend Ruff: passed. Builds used a non-secret `example.invalid` detector-model URL; detector availability itself was not tested.
- PostgreSQL 17.11: all migrations applied to an empty disposable local cluster; schema check clean; migration 0027 downgraded to 0026 and reapplied; schema check clean again.
- Real PostgreSQL integration: **5 passed**. Simultaneous cross-workspace uploads returned **200/413** under a one-image global byte quota and a one-file count cap; simultaneous anonymous push registrations returned **200/429** under a one-record cap. Neither limit was exceeded. Concurrent recording retries and parallel schedule refresh tests also passed.
- Chrome at 390 px: signup stayed disabled before a mocked challenge, submitted its token after completion, cleared it after a deliberately rejected request, and had no horizontal overflow. A screenshot was opened and visually inspected. The provider script and API failure were local mocks; no real Cloudflare challenge or production request was made.
- Browser CSP checks: injected untrusted inline JavaScript was blocked on both main and recorder documents.
- npm audit of the patched lockfile: **zero known vulnerabilities**.
- Full pip-audit resolution of backend requirements: **53 packages, zero known vulnerabilities**. This resolves the local Python/macOS dependency graph; deployed Linux image/OS packages still need their own scan.

The first browser harness used an exact label that did not match the required-field markup; it timed out. The corrected label matched the real accessible label and the complete browser check passed. The first local PostgreSQL start failed because macOS needed `LC_ALL=C`; the corrected start succeeded. An initial downgrade command used an incomplete revision ID and was corrected to `20261017_0026`. These were verification-harness errors, not hidden successful checks.

Two implementation subagents hit a usage limit after leaving edits. The primary agent inspected their changes, corrected test/lint issues and a production-like environment check, and ran the combined verification above. Their failed turns are not represented as successful final reviews. The independent audit and identity implementation were completed by GPT-6.1-Sol.

The final independent GPT-6.1-Sol review found a remaining tiny-file allocation/cardinality gap in the initial photo fix. That finding was repaired with image validation, count ceilings and empty-placeholder cleanup. The independent recheck reran nine photo-storage security tests and reported no remaining required repair in its bounded review. This is a local code review result, not a deployed-system penetration-test certificate.

## Before production

1. Configure a Turnstile widget for the real frontend hostname(s). Keep its secret server-side. Set `TURNSTILE_SITE_KEY` (public site key), `TURNSTILE_SECRET_KEY` and `TURNSTILE_ALLOWED_HOSTNAMES` (comma-separated exact hostnames, no scheme or path) on the backend. The frontend retrieves the public site key from `/workspaces/signup-policy`; do not put the secret into a frontend variable. Production always requires the challenge; `WORKSPACE_SIGNUP_CHALLENGE_REQUIRED=true` can enable it in local/staging verification too. Test keys/hostnames are not production configuration.
2. Apply migration `20261017_0027` before starting the new backend. Existing room tokens must be refreshed by rejoining the room once. Workspace memberships/tokens remain valid. Older reports remain readable and count toward overall coverage, but ambiguous authorship is not guessed from a name: save a new report instead of overwriting an old ID. Queued 409 conflicts require the existing recovery flow.
3. Review the default capacity budgets against real storage and expected traffic. Defaults are 500 MiB of photos per team/event, 1 GiB per workspace, 5 GiB site-wide, a 512 MiB free-disk reserve, 2,000 photos per workspace and 10,000 site-wide. Photos are capped at 16 megapixels; the existing phone uploader resizes before upload. Push defaults are 10,000 retained subscriptions, 300 new registrations/hour and 180 days without an update. These bound damage; they do not guarantee immunity to distributed denial of service or capacity exhaustion.
4. Deploy the reviewed changes, invalidate affected CDN recording responses, and verify two throwaway workspaces through the actual proxy/CDN. A browser-readable private response must never receive shared-cache headers, including duplicate query parameters. Complete a real Turnstile signup and confirm joining an existing workspace still works.
5. Verify live firewall/network exposure, production flags, secret rotation/strength, host and container patch state, backup access and restore capability. These were not inspected by this local source audit. Keep current secrets out of chat and version control.

New budget settings are `PIT_PHOTO_QUOTA_BYTES_PER_WORKSPACE`, `PIT_PHOTO_QUOTA_BYTES_TOTAL`, `PIT_PHOTO_MINIMUM_FREE_BYTES`, `PIT_PHOTO_MAX_FILES_PER_WORKSPACE`, `PIT_PHOTO_MAX_FILES_TOTAL`, `PUSH_SUBSCRIPTIONS_MAX_TOTAL`, `PUSH_SUBSCRIPTIONS_MAX_NEW_PER_HOUR` and `PUSH_SUBSCRIPTION_RETENTION_DAYS`. Existing per-event photo/signup ceilings remain in place. New workspace and push creation fail closed when their shared budget is unavailable in production; existing teams and subscriptions are not reset.

Cloudflare documents [mandatory server-side verification, one-use tokens and expiry](https://developers.cloudflare.com/turnstile/get-started/server-side-validation/). A local audit and passing tests reduce known risk; they are not a guarantee that the deployed website cannot be compromised.
