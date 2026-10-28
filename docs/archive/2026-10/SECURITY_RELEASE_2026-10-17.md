# Security release readiness — 2026-10-17

All six audited fixes are prepared together with the latest main checkout, `55b304c5cfb10a06bbe6c82e67ce46b1d3f68f0d`, in `/Users/jamalmammadzada/Desktop/Projects/FRCMOB-security-release-20261017`. No commit, merge, push or deployment was performed. The earlier [fix report](SECURITY_FIXES_2026-10-17.md) describes the changes and bounded independent review; its test counts refer to the earlier baseline. This report records the combined release candidate.

**Release is blocked on real Turnstile configuration and production rollout approval.** Cloudflare's dashboard was opened, but the browser is signed out. Do not paste its secret into chat or put it in a frontend variable. Local verification cannot establish that a website is impossible to compromise.

## Combined verification

| Check | Observed result |
| --- | --- |
| Backend unit suite | 621 passed, 18 skipped, 2 subtests passed; external connections blocked |
| Frontend suite | 831 passed across 186 test files, Node 26 |
| PostgreSQL and preflight tests | 8 passed: global photo byte/count races, push registration race, recording retries, schedule refresh and preflight checks |
| Migration on disposable PostgreSQL 17.11 | Upgrade, schema check, downgrade to `20261017_0026`, re-upgrade and schema check passed |
| Production frontend build | Passed; non-secret `example.invalid` detector URL used, model availability excluded |
| ESLint / Ruff / whitespace check | Passed |
| Chrome at 390 px | Mock challenge gates submission, token clears after failed creation, no horizontal overflow |
| Browser CSP | Untrusted inline scripts blocked in main and recorder documents |
| Dependency audit | Prior resolution of the identical requirements/lockfile: 53 Python packages and npm graph, zero known vulnerabilities |

The latest-main cleanup removed some tests, so these totals differ from the earlier report. The combined frontend suite initially failed its external-origin inventory because the new Cloudflare privacy-policy link was not classified. The inventory now classifies `www.cloudflare.com` as navigation only; runtime CSP was not broadened for that host. The full rerun passed. Test-only short JWT fixture keys emit warnings; the production preflight checks the configured secret lengths without returning their values.

The real provider challenge, production Linux image/OS scan, full backup restore and post-deployment two-workspace checks remain unverified. A local mocked challenge is not evidence of a working production Cloudflare integration.

## Read-only production observations

Observed around 2026-10-18 03:00–03:52 UTC (October 17 in Chicago):

- Vercel production is READY at `55b304c`, with alias `https://scouting-app-iryg.vercel.app`. Public page and health requests succeeded; deep health reports database and Redis healthy.
- Backend, PostgreSQL and Redis containers report healthy. The backend binds host loopback port 8000; PostgreSQL and Redis have no host-published ports. TLS is public on 443.
- Single TCP connection checks reached 22/443 and timed out on 111/8000/5432/6379. Host iptables rejects unsolicited input after its explicit allowances. This is a narrow exposure check, not a complete firewall audit. `rpcbind` still listens on the host but was unreachable from the tested external path; disabling it, if unused, is a follow-up hardening task.
- Production flags `APP_ENV=production`, strict startup validation, admin write authentication and signed recording sync are enabled. Preflight confirms admin-secret minimum length and explicit CORS configuration without exposing values. Entropy, historical exposure and rotation were not established.
- The production preflight intentionally exits 1: all three Turnstile settings are missing and the running old image has no migration 0027 file. Other checks passed. Database revision is `20261017_0026`.
- Existing backup `frcmob_20261018T0317Z.dump` is 17,510,525 bytes, mode 0600; 72 hourly dumps retained. `pg_restore --list` reads its archive index with 32 table-data entries. No table contents were downloaded and a full restore was not run. Host disk has approximately 69 GiB available.
- Current backend image ID for rollback identification: `sha256:b04bd5118d92ad42f38331ae18a5d3eb05eeb86b2de8f1a0092acfd06ba83e5f`.

## Configuration and release sequence

1. Sign in to the open Cloudflare dashboard. Create/configure a production Turnstile widget for the exact frontend hostname `scouting-app-iryg.vercel.app` and any deliberately supported custom hostname. Use Managed mode. Do not permit arbitrary preview hostnames or use development test keys in production. The client sets action `workspace_create`.
2. Configure backend-only `TURNSTILE_SITE_KEY`, `TURNSTILE_SECRET_KEY` and `TURNSTILE_ALLOWED_HOSTNAMES`. The allowlist is comma-separated hostnames without schemes, paths, ports or wildcards. The site key is public through `/workspaces/signup-policy`; the secret stays server-side. No frontend build variable is required.
3. Review the complete combined candidate and approve publishing. Production main already deploys automatically, so pushing/merging is a release action. Confirm a fresh private backup and ideally restore it into a disposable database before migration. Preserve the current image and matching deployment source before rebuilding. Scan the candidate Linux image and base OS packages.
4. Build the candidate backend image, then run this read-only preflight inside that image with its intended production environment and mounted media volume. It must exit 0 before migration/restart:

   ```sh
   python scripts/security_release_preflight.py \
     --frontend-host scouting-app-iryg.vercel.app \
     --app-root /app --media-root /app/media
   ```

   The script reads process environment only, emits check names/status and remedies, and does not read dotenv files, migrate, contact providers or write files. It checks configuration presence, not whether Cloudflare accepts the keys. Do not dump container environments or include secrets in command arguments/logs.
5. Apply `alembic upgrade head` before starting the candidate backend; confirm revision `20261017_0027`. Deploy the matching frontend promptly and invalidate affected cached private recording responses through the hosting provider. New workspace creation may briefly pause while backend/frontend versions differ; existing joins remain supported. The existing deployment script orders backend build → migration → restart → deep health.
6. Verify through the real Vercel → TLS proxy → backend path using two disposable workspaces: complete a real challenge/signup, join an existing workspace, deny invalid/reused challenge tokens, verify private recording cache headers for boolean aliases/duplicate parameters, reject cross-workspace access, deny recycled-name report overwrites and revoke stale sockets. Verify photo rejection/limits and push validation with bounded test data. Delete only explicitly disposable test records with the required approval.

Existing room tokens require a one-time room rejoin. Workspace membership tokens remain valid. Legacy reports remain readable; ambiguous old authorship is not guessed, so replacing an old ID may return 409 and require a new report. Preserve queued-conflict recovery.

If health or existing joins fail, stop rollout and inspect the concrete failure. Prefer a repair/roll-forward. An emergency application rollback can retain the additive nullable authorship column; **do not downgrade the production database**, which would discard new ownership evidence. Rolling back old application code also restores the old vulnerabilities and may stop accepting new room-token behavior. Keep signup unavailable until the challenge is verified rather than disabling its production enforcement.

Capacity defaults remain adjustable: photos 500 MiB per team/event, 1 GiB per workspace, 5 GiB total, 512 MiB free-space reserve, 2,000 files/workspace, 10,000 total and 16 megapixels/image; push 10,000 retained subscriptions, 300 new/hour and 180-day expiry. These ceilings bound resource use but can still be exhausted by a determined distributed attacker. Add monitoring and budget alerts appropriate to the actual hosting plan before paid promotion.

See [release evidence](SECURITY_RELEASE_EVIDENCE_2026-10-17.json) for source and verification-log hashes. The earlier independent review applies to its recorded source scope; current combined validation was performed by the primary agent, not represented as a new independent review.

## Live verification — 2026-10-18 05:10–05:25 UTC

Deployed state: backend image `sha256:04cdee0e…` on migration `20261017_0027`, deep health green, frontend production deployment `scouting-app-iryg-1v3v7lref` (CSP includes `challenges.cloudflare.com`). Backend logs clean since restart.

- **Signup gate:** `/workspaces/signup-policy` = required with the production site key. Create with no token, a fake token, or Cloudflare's dummy token → 403. In real Chrome (390 px) the widget rendered and gave the automated browser an interactive "Verify you are human" box with Create disabled. **A human-solved signup and the token-replay check are still open** (needs a person; bots are refused by design).
- **Two-team isolation (30/30 checks, public Vercel → Caddy → 2 workers):** workspaces created with the admin key (bypasses the challenge by design). Join by code works. Team B cannot list, read, edit or delete A's picklist (404), cannot see A's pit notes, cannot delete A's photo, cannot read or write A's room even holding A's room token. Photos: fake JPEG header and truncated JPEG → 400, 20,000×1,000 px → 413, real JPEG → 200; signed URL 200, unsigned 403. Workspace heatmap (`include_workspace` = true/1/on/duplicates) → `private, no-store`, never a CDN HIT; without credentials → 401. Removed member's workspace token → 401; recycled name cannot overwrite the old report (409); old room token refused for the new member (403) and on the socket (handshake 403); new member's own socket gets its snapshot. Push: junk keys and non-push endpoints → 400.
- **Exposure:** from outside only 22 and 443 answer (80, 111, 5432, 6379, 8000 closed). `rpcbind` disabled and masked on the host (no NFS mounts; undo with `systemctl unmask` + `enable --now rpcbind.socket rpcbind.service`).
- **Cleanup:** fresh backup, then workspaces 26/27 and their rows deleted in one guarded transaction; 0 "(delete me)" workspaces remain; the test photo was removed through the API.

**Git risk:** production runs code that exists only in the uncommitted worktree `FRCMOB-security-release-20261017` (branch `security/release-2026-10-17`). The next push to `main` redeploys the frontend from `main` and would drop the Turnstile UI and CSP change; a CI deploy from `main` would ship the backend without these fixes and `ci-deploy.sh` would prune their new files. Commit and merge the release before anything else lands on `main`.
