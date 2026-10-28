# Oracle container registry archive (deleted 2026-10-18)

Before deleting the two Oracle Cloud container-image repositories left over from the retired OCI Container Instance deployment (Feb–Apr 2026), every image in them was inventoried and its application source saved. Nothing pulled these images any more: production builds its image on the VM since 2026-09-24, and the container instance was deleted on 2026-10-06.

- **Repositories:** `myapp-api` (91 manifests, 5.85 GB; was **public** until 2026-10-18) and `myapp-worker` (9 manifests, 1.36 GB, private). Registry `phx.ocir.io/axdzkwqzv9hk`.
- **Contents:** 34 builds pushed between 2026-02-27 and 2026-04-22 (all linux/arm64), 100 unique layers (6.74 GB compressed): 67 filesystem layers and 33 SLSA build-provenance attestations.
- **Secrets check:** every filesystem layer was listed file by file. No `.env`, private key, `.pgpass`, `.netrc` or database dump in any image (the only hits were library CA bundles and test certificates). Image configs carry only standard Python base-image variables (`PATH`, `LANG`, `GPG_KEY`, `PYTHON_VERSION`, `PYTHON_SHA256`, pip flags, `PYTHONUNBUFFERED`, `PYTHONPATH`). So the public `myapp-api` exposed old code, not credentials; no key rotation needed on its account.
- **Source of each build:** the provenance records the Git commit. None of those 29 commit IDs exist in today's history, so it was rewritten after these builds and the code lives under new IDs (`docs/PUBLISHING.md` describes the history-rewrite procedure for this repository; whether that is what happened isn't recorded). The "nearest commit" column is the last commit in today's `main` committed before the build started; treat it as an approximation (a build can also include uncommitted changes, which is why the source was saved).

## Where the full archive is

Private Object Storage bucket `frcmob-media-backup`, prefix `archive/ocir-2026-10-18/` (139 objects, 12 MB, verified 0 differences after upload; the VM's policy can't delete it):

- `app_src/<layer>.app.tar.gz` — the `/app` directory of every layer that touched it (35 layers, 3,907 files): source, configs, `requirements.txt`. Left out: compiled `__pycache__`/`.pyc` (179 MB, regenerable) and `yolo11n.pt` (Ultralytics' public starter model, 5.6 MB, repeated in 30 layers).
- `listings/<layer>.txt.gz` — every file in every filesystem layer (mode, size, path); `listings/<layer>.attestation.json` — build provenance, secret-looking values redacted.
- `builds.json` (table below, full), `images.json` (every manifest: layers, entrypoint, cmd, ports, labels, env names with values redacted, build-step history redacted), `layers.json`, `suspects.json`.

Download: `oci os object bulk-download --bucket-name frcmob-media-backup --prefix archive/ocir-2026-10-18/ --download-dir <dir>` (signed in), or `sudo /usr/local/bin/rclone copy oos:frcmob-media-backup/archive/ocir-2026-10-18 <dir>` on the VM. To see a build's code, find its app layer in `images.json` (the last layers of that image) and untar the matching `app_src` file.

## Builds

| Pushed (UTC) | Repo | Tag | Image digest | Original commit | Nearest commit today | Python | Size | Command |
|---|---|---|---|---|---|---|---|---|
| 2026-02-27 04:48:18 | myapp-api | — | `2fa6ea534bcf` | `8a82b80a` | `1b0c85b4` asd | 3.11.14 | 693 MB | `sh -c uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers ${UVICORN_WORKERS:-2}` |
| 2026-02-27 04:53:25 | myapp-worker | — | `7bf21fa060c7` | `8a82b80a` | `1b0c85b4` asd | 3.11.14 | 687 MB | `python worker.py` |
| 2026-03-05 07:21:08 | myapp-api | — | `6aec9c6520cd` | `47af1a96` | `ebbf44df` final before comp | 3.11.15 | 693 MB | `uvicorn app.main:app --host 0.0.0.0 --port 8000` |
| 2026-03-05 23:21:00 | myapp-api | — | `134f4be963b7` | `594f6da5` | `2963674b` asd | 3.11.15 | 693 MB | `uvicorn app.main:app --host 0.0.0.0 --port 8000` |
| 2026-03-05 23:56:29 | myapp-api | — | `cacb6331da74` | `983120dc` | `38cb27bb` asd | 3.11.15 | 693 MB | `uvicorn app.main:app --host 0.0.0.0 --port 8000` |
| 2026-03-06 00:11:00 | myapp-api | — | `b419a2d2f2bf` | `2e5dd227` | `e05516cc` sigma | 3.11.15 | 693 MB | `uvicorn app.main:app --host 0.0.0.0 --port 8000` |
| 2026-03-06 22:37:04 | myapp-api | — | `cbaaa396eb8a` | `b9caba14` | `b6dbb5c1` asd | 3.11.15 | 693 MB | `sh -c uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers ${UVICORN_WORKERS:-2}` |
| 2026-03-06 22:38:24 | myapp-api | — | `3e35d78939ad` | `b9caba14` | `b6dbb5c1` asd | 3.11.15 | 693 MB | `sh -c uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers ${UVICORN_WORKERS:-2}` |
| 2026-03-07 00:10:37 | myapp-api | — | `007364eb6406` | `086bfe92` | `4789f9a3` asd | 3.11.15 | 693 MB | `uvicorn app.main:app --host 0.0.0.0 --port 8000` |
| 2026-03-07 00:31:51 | myapp-api | — | `4c96334cd296` | `f1f9c86f` | `001b1259` asd | 3.11.15 | 693 MB | `uvicorn app.main:app --host 0.0.0.0 --port 8000` |
| 2026-03-07 06:04:19 | myapp-api | — | `84a331ca2702` | `3072dcb1` | `c05a4563` asd | 3.11.15 | 693 MB | `sh -c uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers ${UVICORN_WORKERS:-2}` |
| 2026-03-07 06:12:57 | myapp-worker | — | `f3894c383edd` | `4aee76a5` | `8cbfc053` sigma mode | 3.11.15 | 693 MB | `python worker.py` |
| 2026-03-07 06:53:42 | myapp-api | — | `28e48432147c` | `9740620c` | `4529435f` Harden scouting room create path and service-worke | 3.11.15 | 693 MB | `uvicorn app.main:app --host 0.0.0.0 --port 8000` |
| 2026-03-07 06:56:31 | myapp-api | — | `708c487e7bce` | `ff4c1c9a` | `ffc47054` Handle scouting room table privilege failures with | 3.11.15 | 693 MB | `uvicorn app.main:app --host 0.0.0.0 --port 8000` |
| 2026-03-07 07:12:57 | myapp-api | — | `4d5fa1d2ac6c` | `f633c440` | `176740d7` Treat scouting room schema drift as service unavai | 3.11.15 | 693 MB | `uvicorn app.main:app --host 0.0.0.0 --port 8000` |
| 2026-03-07 07:14:08 | myapp-api | — | `0ea09432d014` | `7fdacdfb` | `c106cde9` Return explicit 503 for room-create SQL failures | 3.11.15 | 693 MB | `uvicorn app.main:app --host 0.0.0.0 --port 8000` |
| 2026-03-07 07:36:09 | myapp-api | — | `8022b056bd93` | `76e216f3` | `e1016114` Handle Oracle DatabaseError in scouting room creat | 3.11.15 | 693 MB | `uvicorn app.main:app --host 0.0.0.0 --port 8000` |
| 2026-03-07 07:49:23 | myapp-api | — | `390c31f96a33` | `5b335657` | `37d62800` Handle Oracle ORA-00942/01031 without table names | 3.11.15 | 693 MB | `uvicorn app.main:app --host 0.0.0.0 --port 8000` |
| 2026-03-07 07:52:11 | myapp-api | — | `2bfa2a1ade4f` | `20fbc9f5` | `79040574` Degrade room-create state reads on DB drift | 3.11.15 | 693 MB | `uvicorn app.main:app --host 0.0.0.0 --port 8000` |
| 2026-03-07 07:56:06 | myapp-api | — | `0db5bf1588ff` | `50e367f1` | `d2269af6` Fail soft when room-state touch writes are unavail | 3.11.15 | 693 MB | `uvicorn app.main:app --host 0.0.0.0 --port 8000` |
| 2026-03-07 07:58:04 | myapp-api | — | `e5c6e969ebcb` | `02c001b9` | `67ac5639` Prevent room-state GET 500 on activity-touch failu | 3.11.15 | 693 MB | `uvicorn app.main:app --host 0.0.0.0 --port 8000` |
| 2026-03-07 08:13:03 | myapp-api | — | `056217c77ca4` | `c9df8202` | `1c05e8c8` Make SQLAlchemy Neon pooler-safe by default | 3.11.15 | 693 MB | `uvicorn app.main:app --host 0.0.0.0 --port 8000` |
| 2026-03-07 08:19:56 | myapp-api | — | `ae25da1c9a11` | `3a7591c6` | `da68f9bd` Return explicit Neon quota errors for DB failures | 3.11.15 | 693 MB | `uvicorn app.main:app --host 0.0.0.0 --port 8000` |
| 2026-03-07 19:46:59 | myapp-api | — | `bb32ca7570cb` | `e51b93a5` | `b1fe2fd1` asd | 3.11.15 | 693 MB | `sh -c uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers ${UVICORN_WORKERS:-2}` |
| 2026-03-07 19:47:12 | myapp-worker | latest | `54e0795b71cb` | `e51b93a5` | `b1fe2fd1` asd | 3.11.15 | 693 MB | `python worker.py` |
| 2026-03-07 21:54:29 | myapp-api | — | `2573d1ccf7f9` | `df9119c7` | `c5e95a26` wroking | 3.11.15 | 693 MB | `sh -c uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers ${UVICORN_WORKERS:-2}` |
| 2026-03-08 05:28:16 | myapp-api | — | `5a9df87cd2d9` | `99acdb55` | `3cd8692e` working maybe | 3.11.15 | 693 MB | `sh -c uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers ${UVICORN_WORKERS:-2}` |
| 2026-03-08 06:13:53 | myapp-api | — | `a3f759500e7c` | `80480b40` | `73c44d02` working hopefully | 3.11.15 | 693 MB | `sh -c uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers ${UVICORN_WORKERS:-2}` |
| 2026-03-08 06:35:54 | myapp-api | — | `e1d9fdfd884b` | `80480b40` | `73c44d02` working hopefully | 3.11.15 | 693 MB | `sh -c uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers ${UVICORN_WORKERS:-2}` |
| 2026-03-08 18:13:26 | myapp-api | — | `2aa3071ee77e` | `6391e47b` | `079c9265` Version 1.0 | 3.11.15 | 694 MB | `sh -c uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers ${UVICORN_WORKERS:-2}` |
| 2026-03-08 19:46:40 | myapp-api | — | `2f8d99f9aebd` | `089a8fe4` | `486ac760` wokring rooms hopefully | 3.11.15 | 693 MB | `uvicorn app.main:app --host 0.0.0.0 --port 8000` |
| 2026-03-09 05:59:16 | myapp-api | — | `b2dc2da26d0e` | `a4677c9f` | `dcb4f84b` ml MAYBE working | 3.11.15 | 694 MB | `sh -c uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers ${UVICORN_WORKERS:-2}` |
| 2026-03-09 18:02:03 | myapp-api | latest | `9c82b0251d99` | `b350a303` | `efae732b` asd | 3.11.15 | 694 MB | `uvicorn app.main:app --host 0.0.0.0 --port 8000` |
| 2026-04-22 02:11:21 | myapp-api | ec05ab66 | `a67639ba7e77` | `—` | `3dd27c44` deleted outdated docs | 3.11.15 | 3392 MB | `sh -c uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers ${UVICORN_WORKERS:-2}` |
