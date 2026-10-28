# Keeping this repo safe to publish

This repository is public, and production deploys straight from `main`. Anything pushed here, on any branch or in any pull request, should be treated as published for good.

## Never commit

- Credentials of any kind: API keys, tokens, passwords, SSH keys, `.env` files. Example env files (`*.example`) hold placeholders only.
- Maintainers' private operating notes (they live outside the repo).
- Model weights, labeled datasets, match recordings, database exports, logs, team member data, or FIRST game manuals. See [THIRD_PARTY_ASSETS.md](THIRD_PARTY_ASSETS.md).

## What checks for leaks

- GitHub secret scanning with push protection refuses pushes that contain known token formats.
- gitleaks scans the whole history on every pull request and push to `main` (the `secrets` job in CI, rules in `.gitleaks.toml`), and staged changes before each commit once the hook is on (`git config core.hooksPath .githooks`). The allowlist only drops FRC match/event/team keys, snake_case identifiers and published checksums; keep it that narrow.
- Dependabot alerts and security updates cover the dependencies.

## If a secret gets committed

1. **Rotate it first.** Assume it was copied the moment it was pushed.
2. Then remove it from the branch. Rewriting history doesn't make it secret again: forks, clones and GitHub's pull-request refs keep the old commits. GitHub Support can purge cached views ([removing sensitive data](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository)).
3. Say in the fixing pull request what was rotated.

## History of this repo

Until 2026-10-28 development happened in a private repository, and this one received `git archive` snapshots (the "Sync from upstream" commits). Since then this is the only repository. The private history is not published: it holds third-party files that can't be redistributed.
