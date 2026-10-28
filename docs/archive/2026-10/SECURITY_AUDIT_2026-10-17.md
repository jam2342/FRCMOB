# Pre-launch security audit — 2026-10-17

**Update 2026-10-18: all six fixes are deployed and verified live (30/30 checks); see [release readiness](SECURITY_RELEASE_2026-10-17.md).** Earlier: all six findings got locally verified fixes in the isolated security checkout. See [fix results and launch prerequisites](SECURITY_FIXES_2026-10-17.md). The historical findings below describe the audited vulnerable baseline. Hold launch until deployment and operational checks are complete.

Audited baseline: `de8ebab9a6dd0486a7810e28505f5b0d79d6a1a8` (PR #199). The workspace was clean initially; unrelated team-breakdown/quality-gate edits appeared during the audit and were preserved. Findings refer to the security-sensitive files inspected and the local reproduction results, not certification of the evolving checkout or deployed infrastructure.

The primary agent reviewed public write paths, storage limits, push delivery, middleware, startup/deployment configuration, browser injection defenses and dependencies. An independent GPT-6.1-Sol subagent reviewed authentication, tenant isolation and room identity and reproduced findings F1, F4 and F5. The local `ai-collab` runner was attempted but refused model preflight: `the current OpenAI model guide did not expose a Sol model; refusing to guess model names`. No runner workers started and no runner integration or acceptance is claimed. The independent review used the available internal GPT-6.1-Sol subagent.

## Findings

| ID | Severity | Verified issue | Attacker prerequisite |
| --- | --- | --- | --- |
| F1 | High | Private workspace recordings can be marked share-cacheable | A member first requests a specially formed URL; disclosure to another visitor depends on CDN behavior |
| F2 | High | Arbitrary event names bypass the effective photo-storage budget | One freely created workspace |
| F3 | Medium | Anonymous push registrations cause persistent database growth and scheduled upstream work | No credentials |
| F4 | Medium | Reusing a removed scout's name grants edit access to their existing reports | An ordinary current teammate and an available former scout name |
| F5 | Medium | Open sockets keep submitting under a released name after profile rename | An ordinary current teammate with an open room socket |
| F6 | Medium | One caller can consume the entire site's hourly signup allowance | No credentials |

### F1 — Private recordings receive shared-cache headers

References: `backend/app/api/edge_cache.py:115`, `:121`, `:139`, `:227`; `backend/app/api/routes_tracks.py:118`, `:128`, `:201`; `ScoutingApp/vercel.json:58` (API rewrite caching).

FastAPI accepts `include_workspace=1`, `yes`, and `on` as true. The cache policy recognizes only the literal string `true`. It also examines the first repeated parameter, while FastAPI uses the last. Requests carrying `X-Workspace-Access` do not independently disable shared caching.

Using a real provisional recording with two private points, the real tracks route and cache middleware returned:

| Query | Private points | Cache-Control | CDN-Cache-Control |
| --- | ---: | --- | --- |
| `include_workspace=true` | 2 | `private, no-store` | absent |
| `include_workspace=1` | 2 | `no-cache` | `max-age=60, stale-while-revalidate=600` |
| `include_workspace=yes` | 2 | `no-cache` | same |
| `include_workspace=on` | 2 | `no-cache` | same |
| `include_workspace=false&include_workspace=true` | 2 | `no-cache` | same |

The response has no workspace-based `Vary`. This proves incorrect origin cache classification of tenant data. A live CDN cache hit exposing another team's response was deliberately not attempted. Exploitation requires a victim/member request in a vulnerable query form to populate a shared cache; it does not let an unauthenticated caller directly query another workspace at the origin.

**Fix:** suppress shared caching whenever workspace credentials or any `include_workspace` query parameter is present. Preserve explicit private classification made by handlers. Cover alternate booleans, repeated parameters and both heatmap and shift-play routes. After deployment, invalidate any affected cached responses and verify with two throwaway workspaces through the real proxy/CDN.

### F2 — Per-event photo quota is bypassable with invented events

References: `backend/app/api/routes_pit_scouting.py:46`, `:174`, `:198`; `backend/app/services/workspace_limits.py:80`; `backend/app/db/models.py:702`.

Photo storage is capped per workspace **and client-supplied event name**, with a default of 500 MiB per event. Event names need only match a regex. Pit entries intentionally have no event foreign key, and no cumulative workspace, site-wide or free-disk budget is enforced here. A caller can create new valid-looking event names indefinitely using one workspace. The signup cap and per-photo request limit do not bound that workspace's total disk use.

**Observed:** with an isolated 66-byte quota and no events in the database, the first photo for `2026auditone` returned 200; another at that event returned 413; changing only the event to `2026audittwo` returned 200. Actual temporary files totaled 132 bytes. Scaling was not load-tested and no disk was filled.

**Impact:** a freely registered user can keep writing uploads until storage is exhausted, affecting recordings/photos and potentially other VM services sharing the disk.

**Fix:** enforce cumulative workspace and site-wide storage budgets with concurrency-safe accounting and a minimum-free-space reserve. Add upload-rate/byte budgets. Continue supporting legitimate pre-ingest pit scouting; requiring an existing event alone would break that feature and would not replace a cumulative budget. Clean up files when DB commits fail.

### F3 — Anonymous push subscriptions persist arbitrary records and amplify scheduler work

References: `backend/app/api/routes_push.py:76`, `:112`; `backend/app/services/push/match_alerts.py:65`, `:81`, `:85`; `backend/app/db/models.py:728`.

The push endpoint allowlist blocks arbitrary-host SSRF, which is good. However, anyone can register made-up URLs on an allowed push host, nonempty invalid encryption keys, and invented event names. Subscription creation has no count/rate budget or creation expiry. Key strings have no explicit size/encoding bounds beyond the overall request-body cap. The scheduler loads every enabled event subscription and requests match data once per distinct event, even when that event has no local matches. Invalid-event subscriptions are skipped later without being disabled, so their work can recur.

**Observed:** three unauthenticated registrations with different fake FCM endpoints, invalid keys and invented events all returned 200 and persisted three enabled rows. One scheduler tick attempted three event lookups, intercepted with a mock; no upstream call actually left the process.

**Fix:** validate push key encoding/length, bound registration rates and retained subscriptions, enforce an expiry/cleanup policy, and cap scheduler work. Skip or negatively cache unknown/inactive event requests. Preserve anonymous fan notifications with a bounded installation capability or another appropriately scoped ownership mechanism. Per-endpoint limits on `/push/test` do not limit new subscriptions.

### F4 — Display-name recycling grants another member report ownership

References: `backend/app/api/routes_scouting_rooms.py:1010`, `:1032`; `backend/app/api/routes_workspaces.py:197`; `backend/app/services/workspaces.py:148`.

Report replacement authorizes the caller by comparing `existing.scout_profile` to the current display-name string, plus a newer client-controlled `saved_at_ms`. A removed member's name becomes available to another member, while their reports retain the old name and visible client entry IDs.

**Observed through real APIs:** member 2 (`Scout`) saved a 10-point report. The leader removed member 2 and rotated the join code. Existing ordinary member 3 (`Other`) renamed to `Scout`, joined the room and submitted the visible report ID with a larger timestamp and 999 points. The server returned 200 with `created: false`; the original report was overwritten. The attacker did not need the rotated join code or leadership.

**Fix:** bind report authorship and update authorization to immutable workspace member IDs/keys. Names are display labels. Legacy reports with ambiguous ownership must not be claimable solely by name; provide an explicit leader correction/recovery path. Any migration and legacy attribution policy require a deliberate reviewed change.

### F5 — WebSockets retain a released name after rename

References: `backend/app/api/routes_scouting_rooms.py:2002`, `:2091`, `:2173`; `backend/app/api/routes_workspaces.py:197`.

The socket verifies a scout name when connecting. On subsequent messages it refreshes the member but checks only removal. A profile rename leaves the socket authorized under the old name, which is now available to someone else.

**Observed:** a socket connected as `Scout`; the same member successfully changed their name to `Renamed` over HTTP. A subsequent socket `save_entry` received `entry_ack` and persisted `scout_profile=Scout`, while the member row's current name was `Renamed`.

**Fix:** invalidate/close sockets on rename across all workers, and compare the bound identity with the refreshed member before processing each message. Store immutable author identity. Verify that stale room tokens and active sockets cannot impersonate whoever later acquires the old name.

### F6 — Global signup budget can be exhausted by one anonymous caller

References: `backend/app/services/workspace_limits.py:33`; `backend/app/api/routes_workspaces.py:125`.

The site-wide hourly allowance caps cost, but one caller can consume it. There is no caller-level friction before consuming a slot. The default is 30 creations per hour. On Redis failure the limiter fails open, which is a separate loss of the cost cap rather than a proven Redis exploit.

**Observed:** lowering the allowance to two in the isolated proof, three anonymous create requests returned 200, 200, 429. The counter is site-wide, so the next legitimate team's request shares that exhausted budget.

**Fix:** retain a global emergency ceiling, and add per-installation/caller abuse controls or a challenge for excessive creation. Establish a trustworthy client identity through the actual proxy chain before relying on forwarded headers; user-supplied `X-Forwarded-For` is insufficient. Choose an intentional degraded-mode policy for Redis loss.

## Dependency results

- Frontend lockfile audit: one high advisory for **development/build dependency** `source-map-js@1.2.1`, GHSA-68fv-2mgg-jv7q / CVE-2026-93749. `npm explain` located it under PostCSS/Vite and jsdom tooling. Patched version is 1.2.2. This is not evidence that the static production website exposes the vulnerable parser. Update the lockfile and rerun the build before release. [Advisory](https://github.com/advisories/GHSA-68fv-2mgg-jv7q).
- Backend direct pinned requirements: `pip-audit --disable-pip --no-deps` reported no known vulnerabilities. **Transitive dependencies were not resolved/audited by that command.** The backend has no fully resolved transitive lockfile here; do a complete deployed-image/SBOM audit before treating dependency scanning as complete.
- No dependency files were changed and no packages were installed by this audit.

## Verification and evidence

The permanent proof script is `backend/scripts/security_audit_20261017.py`. It blocks socket connections, refuses inherited app settings, loads settings without dotenv, uses in-memory SQLite with foreign keys enabled for resource proofs, temporary photo files and test fixtures. It exercises real route handlers and middleware. Mocked Redis/upstream services isolate budget accounting and count attempted work. It performs no load test, writes no production data and prints no access tokens.

From the repository root:

```sh
env -i PATH=/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin \
  .venv/bin/python backend/scripts/security_audit_20261017.py
```

**The script asserts the vulnerable baseline behavior. Exit 0 means reproduction succeeded, not that the app is safe.** Convert its cases into prevention regression tests as fixes are made.

Results on this audit:

- All six issue reproductions succeeded. Two 66-byte photo files were created in a temporary directory and removed automatically.
- Existing backend security selection: **82 passed** (auth/write guards, startup, workspace boundaries, room permissions, on-device sync security, abuse limits and edge caching).
- Frontend security selection: **27 passed** across CSP, embed-host validation, workspace sessions, secure native credential handling and service-worker caching. Nonfatal warnings included repeated SecureStorage mock registration and Starlette's httpx deprecation.
- A production frontend build from a clean tracked-source snapshot succeeded. No dotenv files were copied/read. A non-secret `example.invalid` model URL was supplied to validate the build; detector functionality was not checked.
- Generated `index.html` and `record.html` were opened and inspected: both contain CSP, all existing inline-script hashes match, and unrestricted JavaScript `unsafe-eval` is absent. `record.html` has no inline scripts and correctly needs no hash.
- Proof script Ruff check and `git diff --check` passed.

The first backend collection attempt used in-memory SQLite for the app's global engine and failed because PostgreSQL-oriented pool arguments are incompatible with that SQLite pool. The corrected verification used a dummy unreachable local PostgreSQL engine configuration, while test fixtures provided the actual isolated SQLite engines. No production config was needed. An initial npm audit invocation also rejected using `/dev/null` for both user and global config; separate empty config paths resolved it. These were harness failures, not application findings.

## Controls reviewed and remaining limits

Existing protections include HS256 algorithm pinning and required JWT timing/type claims, member revocation checks against the database, workspace-scoped picklists/pit notes, immutable reviewed recordings, upload/request size bounds, push-service URL allowlisting, signed pit-photo URLs, browser CSP, clickjacking headers, parameterized ORM queries, and nonpublic Postgres/Redis ports in the OCI compose definition. No practical SQL injection, arbitrary-code execution or arbitrary-host push SSRF was found in this targeted review. This does not prove their absence.

Live firewall rules, deployed environment flags/secret strength, host patch state, actual TLS/CDN behavior, full transitive/container vulnerabilities, backup access controls and credential-rotation status remain unverified. No secrets, `.env`, `Oracle/`, production databases or private user data were inspected. Historical credential-exposure notes in project instructions are not evidence that current credentials are rotated. Those operational checks and a two-workspace staging retest are still needed before launch.

OWASP's [API resource-consumption guidance](https://api-security.owasp.org/editions/2023/en/0xa4-unrestricted-resource-consumption/) supports treating persistent storage and scheduled fan-out as abuse boundaries. [Vercel cache-control documentation](https://vercel.com/docs/caching/cache-control-headers) explains the shared-cache header used in F1; actual edge behavior still requires a safe staging test.

Security-control, dependency, migration and deployment changes were left unapplied because the supplied working instructions require an explicit approval event for those changes. Nothing was committed, pushed or deployed by this audit.
