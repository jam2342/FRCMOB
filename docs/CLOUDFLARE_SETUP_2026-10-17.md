# Cloudflare setup — October 17, 2026 (Chicago)

The Cloudflare OAuth login completed successfully; the Codex command returned `Successfully logged in.` The consent was narrowed to Turnstile Sites Read/Write, Account Read, User Read and Background Access. The user performed the final authorization themselves. No API token was requested or exposed.

All 19 skills from Cloudflare's official repository were installed and their SKILL.md files verified. Source revision: `86505e44f87d801aa1864a3c4109e9a8ae5c409f`. Codex's global MCP configuration points to `https://mcp.cloudflare.com/mcp`, with no configured bearer-token/header helper. The optional beta CLI was not installed.

A new widget was created in the signed-in Cloudflare dashboard and verified in its saved widget list:

| Setting | Saved value |
| --- | --- |
| Name | FRCMOB workspace signup |
| Public site key | `0x4AAAAAAFZ0yjah0cDQA2SW` |
| Allowed hostname | `scouting-app-iryg.vercel.app` |
| Mode | Managed |
| Pre-clearance | No pre-clearance |

The host was selected explicitly, and the form confirmed one configured hostname before creation. The saved list confirms one hostname, Managed mode and no pre-clearance. The success notification confirmed widget creation. No replacement/duplicate widget was created.

The Cloudflare MCP connection is now loaded and its authenticated identity and saved widget metadata were verified through the API. The user explicitly approved retrieving this existing widget's secret, validating it with Cloudflare, and appending exactly three settings to `/home/ubuntu/frcmob/.env` on the Oracle VM. That update is complete. The file remains mode 0600, and existing settings were preserved. No frontend/backend deployment, service restart, production database migration, commit, merge or push was performed.

## Secret storage verification

The transfer used the authorized OAuth MCP connection and an echo-disabled standard-input channel to SSH. The secret was excluded from displayed tool output, command arguments, exported shell variables, temporary files and this report. It was stored only in the approved existing backend secret store. The remote guard validated the widget's exact sitekey, single hostname, Managed mode and no-pre-clearance setting, then sent a deliberately invalid token to Cloudflare Siteverify. Cloudflare returned the expected `invalid-input-response` without `invalid-input-secret` before any write was made.

The initial append was refused because the parent directory is mode 0775. Read-only inspection established that its group is the owner's primary group and has no other members, with no extended access ACL. The corrected guard permits this owner-only group while rejecting shared groups, world writes, extra ACLs, symlinks and overwrites. Its preservation, no-overwrite, group and ACL checks passed on disposable fixtures. The Linux xattr API was mocked for the macOS fixture run; the actual Linux directory/file checks passed on the VM. The successful retry appended these settings once each:

- `TURNSTILE_SITE_KEY`: matches the public sitekey above.
- `TURNSTILE_SECRET_KEY`: present; value never displayed.
- `TURNSTILE_ALLOWED_HOSTNAMES`: exactly `scouting-app-iryg.vercel.app`.

Read-back verified all three once each, exact public key/hostname and mode 0600. Backend deep health remained green, and the running image ID remained `sha256:b04bd5118d92ad42f38331ae18a5d3eb05eeb86b2de8f1a0092acfd06ba83e5f`. The running process has not reloaded these settings. The dummy-token probe establishes secret validity, not a successful real signup or replay test.

## Backup restore verification

Backup `frcmob_20261018T0317Z.dump` was fully restored into a disposable PostgreSQL 17 container on the VM, with `--network none`, no published ports, a 1 GiB memory limit and one CPU. Backup data stayed on that VM, passing through memory into the disposable container's tmpfs. Production PostgreSQL was not modified. Restore used `--exit-on-error --single-transaction --no-owner --no-acl`; it completed with 32 public base tables and revision `20261017_0026`. This verifies schema/data restore, not recovery of original role ownership or ACLs.

The first disposable container did not become ready under the stricter initial root-filesystem/tmpfs profile; no restore ran in that attempt. A standard ephemeral root filesystem with bounded tmpfs and memory then initialized and completed the restore. Both test containers were removed, and no restore-test container remained afterward.

## Remaining work

1. Approve and perform the reviewed release procedure. Build and scan the candidate Linux image, run its preflight against the configured environment, apply migration 0027, then deploy/restart the matching backend and frontend. Local Docker is unavailable, so the candidate Linux-image build/scan has not run locally. The earlier frontend build used a placeholder model URL for verification; use the hosting platform's actual production configuration for the release build.
2. Exercise the actual backend with a fresh real challenge token, then verify replay rejection and the other live checks in [release readiness](SECURITY_RELEASE_2026-10-17.md). No end-to-end provider success is claimed yet.

This record supersedes the earlier report's signed-out/no-widget/missing-secret and untested-restore observations. The widget's settings are stored for the next backend startup, but the new security code is not yet deployed and signup protection is not active in production.
