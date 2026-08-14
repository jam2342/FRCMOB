# Training the FRC robot detector (`frc_robot_detector_v1.pt`)

The pipeline's tracking stage needs an FRC-specific YOLO detector. The generic
COCO `yolo11n.pt` barely detects competition robots, so without this model every
analysis run is (correctly) flagged `DEGRADED` and produces empty/low findings.

This model is **not** in the repo and was never committed — it must be trained.
All the tooling exists; this doc is the reproducible workflow.

---

## Status & honest benchmark (READ THIS)

| Model | Status | Honest benchmark (locked Einstein holdout) |
|---|---|---|
| `frc_robot_detector_v2.pt` | **Deployed (primary)** | **mAP@0.5 = 0.7172** |
| `frc_robot_detector_v1.pt` | Shipped in image as rollback | mAP@0.5 = 0.6991 |

- v2 was a low-LR fine-tune from v1 on the v2-mixed dataset (7,634 train / 339
  val); the aggressive 30-epoch candidate was correctly rejected at 0.6740 by
  the same holdout gate.
- **The honest numbers are 0.7172 (v2) and 0.6991 (v1).** Both come from
  `media/datasets/frc_einstein_2026_holdout_reviewed_yolo` (224 reviewed
  images, Einstein 2026 — footage neither model trained on).
- **Do NOT report 0.98.** The ~0.98 figure (Roboflow-leaked split *and* the
  later "clean" in-distribution re-split) only measured performance on the
  narrow 42-scene training distribution. It is retired.
- **Deployment (bake path, hot-swap ready):** both v1 and v2 are whitelisted in
  `.dockerignore` and ship in the image. `VIDEO_TRACKING_YOLO_MODEL` selects
  which is primary — change the env var to roll back, no rebuild required.
  To move to object storage instead, set `VIDEO_TRACKING_YOLO_MODEL_URL`.
- End-to-end pipeline verification with v2 (held-out YouTube match):
  `model_degraded: false`, quality 0.82, 6/6 findings carry non-zero metrics.
  (Known minor observability gap: per-finding `tracking_backend_meta.model_source`
  isn't always populated when a motion-fallback pass blends in; the run-level
  `model_degraded: false` is the reliable signal that the FRC model was used.)

### 🔒 Locked test-only holdout

`media/datasets/frc_einstein_2026_holdout_reviewed_yolo` is the honest
benchmark. **Never train/split on it.** `source_grouped_split.py` and
`import_external_yolo_labels.py` are **code-guarded** to hard-refuse this path
(see `LOCKED_HOLDOUT_DIR_NAMES`). A v2 replaces v1 **only** if it beats 0.699
here *and* passes the end-to-end pipeline test.

---

## The bootstrap paradox (read this first)

`export_yolo_dataset_from_tracks.py` builds the training set from `RobotTrack`
rows in the DB. But those rows are produced **by the YOLO model**. With no FRC
model, tracking falls back to generic YOLO, finds ~no robots, writes ~no
tracks → empty dataset → can't train.

You must break the cycle **once** with labels from outside the pipeline. After a
v1 model exists, the loop becomes self-improving and external labels are
optional.

```
            ┌─────────────────────────────────────────────┐
            │  external labels (Roboflow / CVAT / manual)   │  ← one-time bootstrap
            └───────────────────────┬─────────────────────┘
                                    ▼
   import_external_yolo_labels.py  →  media/datasets/frc_yolo/
                                    ▼
            train_frc_yolo.py  →  media/models/frc_robot_detector_v1.pt
                                    ▼
        pipeline runs with the FRC model → good RobotTrack rows
                                    ▼
   export_yolo_dataset_from_tracks.py  →  bigger/better dataset
                                    ▼
            retrain (v2, v3, …)  ──────────────► self-improving
```

---

## Step 0 — get bootstrap labels (manual, one time)

You need a few hundred frames of FRC match footage with robots boxed, in
**YOLO detection format** (single class, `0 cx cy w h` normalized). Options:

- **Roboflow**: search "FRC robot" public datasets or upload your own frames and
  label them; export as "YOLOv8". (Existing public FRC robot datasets exist.)
- **CVAT / Label Studio**: import frames, draw boxes, export YOLO format.
- **Reuse your own frames**: extract frames with the existing pipeline
  (`extract_sample_frames`) or `ffmpeg`, then hand-label.

Target: **≥ ~300 labeled images** spanning multiple events/fields/lighting.
Quality and variety matter more than raw count.

## Step 1 — seed the dataset from external labels

```bash
cd backend
PYTHONPATH=. python scripts/import_external_yolo_labels.py \
  --src /path/to/exported_yolo_dataset \
  --output media/datasets/frc_yolo
```

Accepts either split layout (`images/{train,val}`) or flat (`images/`,
`labels/`). All classes are remapped to `0 = robot`. Fails (`ok:false`,
exit 1) if no valid image/label pairs are found.

## Step 2 — (later, optional) augment from your own pipeline

Once a model exists and has analyzed real matches:

```bash
PYTHONPATH=. python scripts/export_yolo_dataset_from_tracks.py \
  --output media/datasets/frc_yolo \
  --only-yolo \
  --min-images 300
```

`--min-images` makes it **fail loudly** instead of silently producing an empty
dataset (the original footgun). `--only-yolo` keeps only model-produced tracks
(skips weak motion-fallback boxes). You can run this repeatedly across events to
grow the set; it merges into the same directory.

## Step 3 — train

```bash
PYTHONPATH=. python scripts/train_frc_yolo.py \
  --data media/datasets/frc_yolo/dataset.yaml \
  --base-model yolo11s.pt \
  --epochs 80 --imgsz 1280 --batch 12 \
  --device 0 \
  --min-map50 0.30 \
  --output-model media/models/frc_robot_detector_v1.pt
```

- `--device 0` for an NVIDIA GPU, `mps` on Apple Silicon, omit for CPU (slow).
- **`--min-map50` is a quality gate**: after training, the script validates the
  weights and **refuses to publish** if validation mAP@0.5 is below the
  threshold. A model that barely detects is worse than the honest DEGRADED
  flag, so don't lower this for production weights.
- Output goes to exactly where the pipeline looks
  (`media/models/frc_robot_detector_v1.pt`, per
  `settings.video_tracking_yolo_model`).

## Step 4 — deploy the weights

`backend/media/` is git-ignored, but `.dockerignore` **already whitelists**
`backend/media/models/frc_robot_detector_v1.pt`, so it bakes into the image if
present at build time. Two supported paths (chosen earlier):

- **Bake**: keep the file at `backend/media/models/frc_robot_detector_v1.pt`
  before `docker build`. Done.
- **Download on startup** (recommended for OCI): upload the `.pt` to object
  storage and set `VIDEO_TRACKING_YOLO_MODEL_URL=<url>` in the deploy env.
  `model_provisioning.ensure_primary_model_available()` fetches it on
  backend/worker startup (atomic write, size-sanity check, never blocks boot).

## Step 5 — verify it's actually used

After deploy, an analysis run's result / each finding's
`tracking_backend_meta` should show `model_source` ending in
`frc_robot_detector_v1.pt` and **`model_degraded` absent/false**. If you still
see `model_degraded: true`, the file isn't reaching the worker — check the
startup log line `worker.model_provisioning {...}`.

---

## v2 (done) and v3+ plan — must beat the locked holdout honestly

**v2 status: trained, gated, deployed.** v2 beat v1 on the locked Einstein
holdout (0.7172 vs 0.6991, +0.018) via low-LR fine-tune from v1. The aggressive
30-epoch candidate failed at 0.6740 and was correctly rejected by the same
gate. v2 ships as primary; v1 stays in the image for instant rollback.

For v3+, the same recipe applies — must beat the **current** deployed
benchmark on the **locked** Einstein holdout. The plan:

1. **Collect 500–1,000+ NEW real broadcast frames.** Multiple events, camera
   angles, lighting, fields. Real footage, not simulator renders. Use v1 or
   Roboflow Label Assist to pre-label and just correct boxes (much faster).
   Label **only `robot`** (single class).
2. **Split by source video/event — never by augmented filename.** Use
   `scripts/source_grouped_split.py` (it groups by source identity so
   augmented/consecutive-frame siblings can't cross train/val — the v1 leakage
   cannot recur). Combine new data with the existing Roboflow set if desired,
   but keep the split source-grouped.
3. **Never touch the locked holdout.** The splitter/importer hard-refuse
   `frc_einstein_2026_holdout_reviewed_yolo`. It stays test-only.
4. **Retrain on A100** (Step 3 below) on the expanded clean dataset. Use a
   key-free bootstrap (do NOT hardcode dataset URLs/API keys in the script —
   the v1 bootstrap leaked a Roboflow key; pass via env var).
5. **Gate on the honest holdout.** Validate v2 against
   `frc_einstein_2026_holdout_reviewed_yolo`. **Replace v1 only if v2 beats
   mAP@0.5 = 0.699 there AND passes the end-to-end pipeline test**
   (`model_degraded:false`, non-zero findings).

## Retraining (v2+)

Once matches have been analyzed with v1, you can also augment the dataset from
the pipeline's own output: rerun Step 2 (export from tracks, now plentiful) →
Step 3 (train) → Step 4. Always validate against the locked Einstein holdout
before replacing the deployed model. Bump the output filename / keep versioned
copies for rollback.

## Gotchas

- **Don't** ship weights that fail `--min-map50`. Empty findings that look
  "successful" are the exact failure mode this whole effort fixed.
- The dataset/model dirs live under `backend/media/` (git-ignored) — they will
  not be committed; that's intentional. Distribution is via image bake or
  object-storage URL, not git.
- Single class only (`0 = robot`). The pipeline's class handling assumes this;
  multi-class detectors need separate wiring.
