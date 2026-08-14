# Publishing FRCMOB safely

Do not change the existing repository to public until its historical artifacts are handled. The current source tree excludes them, but older commits contain `game manual.txt` and `backend/yolo11n.pt`.

## Recommended: publish a clean-history repository

Keep the current private repository as an archive. After committing the reviewed release tree, export that commit into a new repository with a single clean initial commit. Verify the exported file list and rerun secret scanning before adding a public remote. This avoids exposing private collaboration refs and old third-party artifacts.

Do not copy `.git`, `.env`, local media, `Oracle/`, model files, databases, or build output into the new repository.

## Alternative: rewrite the existing history

Only choose this if preserving commit history is important. Back up the repository and coordinate with every collaborator because all rewritten commit IDs change. In a disposable mirror, the removal operation validated for this repository is:

```bash
git clone --mirror <private-repository-url> frcmob-public.git
git -C frcmob-public.git filter-repo --force --invert-paths \
  --path 'game manual.txt' \
  --path 'backend/yolo11n.pt'
git -C frcmob-public.git fsck --full --strict
git -C frcmob-public.git log --all --name-only --format= | \
  sort -u | grep -E '(game manual\.txt|\.pt$)'
```

The final `grep` must print nothing. Then run a full secret scanner against all refs. Inspect and remove local tool-only refs and branches that are not intended for publication. Force-pushing rewritten history is destructive and must be an explicit owner action.

## Visibility gate

Before publishing either form:

1. Rerun the commands in the README from the exact commit that will be public.
2. Confirm the repository license is detected as AGPL-3.0.
3. Enable the GitHub protections in `OPEN_SOURCE_RELEASE_CHECKLIST.md`.
4. Rotate any credential whose secrecy is uncertain.
5. Review the public file list one final time, then change visibility or push the clean repository.
