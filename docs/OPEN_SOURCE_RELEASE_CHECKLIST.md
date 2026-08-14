# Open-source release checklist

## Repository gate

- [ ] Review `git diff` and commit only intended source/readiness changes.
- [ ] Confirm secret scanning is enabled and the full Git history has no credentials.
- [ ] Confirm no model weights, datasets, recordings, database exports, logs, or FIRST manuals are tracked.
- [ ] Run backend lint/tests and frontend lint/tests/build from a clean checkout.
- [ ] Review dependency alerts and third-party notices.

## GitHub settings

- [ ] Enable private vulnerability reporting, secret scanning, push protection, and Dependabot alerts.
- [ ] Protect `main`: require pull requests, CI, review, and conversation resolution; block force pushes and deletion.
- [ ] Set GitHub Actions workflow permissions to read-only by default and allow write scopes only per job.
- [ ] Add a repository description, topics, AGPL-3.0 license label, and public contact/support expectations.

## Before changing visibility

- [ ] Rotate any credential that has ever been committed or shared in an artifact, even if it was later deleted.
- [ ] Decide whether to publish existing history or create a fresh public repository after reviewing historical copyrighted files.
- [ ] Follow `docs/PUBLISHING.md`; the existing history contains an old FIRST manual and YOLO weight.
- [ ] Verify the production frontend contains no credential strings and production CORS/auth/startup checks are strict.
- [ ] Back up the private repository and deployment configuration separately.
