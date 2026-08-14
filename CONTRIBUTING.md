# Contributing to FRCMOB

Thanks for helping improve FRC scouting. Contributions should be focused, testable, and safe for teams to run during an event.

## Before opening a pull request

1. Open an issue for large behavior or schema changes so the design can be agreed first.
2. Branch from `main` and keep unrelated changes out of the pull request.
3. Add or update tests for behavior changes.
4. Run the backend and frontend verification commands in the README.
5. Document new environment variables in `.env.example` without real values.

Never commit credentials, `.env` files, team member data, database exports, match recordings, model weights, labeled datasets, or copies of FIRST manuals. Use synthetic or explicitly redistributable fixtures in tests.

By contributing, you agree that your contribution is licensed under AGPL-3.0, the repository license. Please follow the [Code of Conduct](CODE_OF_CONDUCT.md) and use [SECURITY.md](SECURITY.md) for vulnerabilities.
