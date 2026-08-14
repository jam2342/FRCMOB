# Security policy

## Supported versions

Security fixes are applied to the latest commit on `main`. Older branches and self-hosted deployments should update to the latest release before requesting support.

## Reporting a vulnerability

Do not open a public issue. Use GitHub's private vulnerability reporting feature under **Security → Advisories → Report a vulnerability**. Include the affected version, reproduction steps, impact, and any suggested mitigation. Please avoid accessing other users' data or disrupting a live event while testing.

Maintainers should acknowledge a complete report within seven days. Public disclosure should wait until a fix or agreed mitigation is available.

## Deployment expectations

- Generate unique `ADMIN_API_KEY` and `ADMIN_SESSION_TOKEN_SECRET` values and keep them server-side.
- Never put credentials in `VITE_*`, `NEXT_PUBLIC_*`, browser bundles, URLs, or repository secrets visible to pull requests.
- Enable `STRICT_STARTUP_ENV_VALIDATION=true` and `ENFORCE_ADMIN_AUTH_FOR_WRITES=true` in production.
- Restrict CORS to the deployed frontend origins and use TLS for HTTP, PostgreSQL, and Redis across untrusted networks.
- Treat scouting exports, recordings, push subscriptions, logs, and database backups as potentially sensitive team data.
