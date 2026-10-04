# Training the FRC robot detector (`frc_robot_detector_v1.pt`)

The on-device recorder needs an FRC-specific YOLO detector. The generic COCO
`yolo11n.pt` barely detects competition robots. The server-side broadcast pipeline
that also used this model was retired on 2026-09-28; the detector now ships to phones
as ONNX (`scripts/export_on_device_model.py`). Install the training dependencies with
`pip install -r backend/requirements-training.txt`.

Sections below that describe baking the `.pt` into the server image, the worker, or
`VIDEO_TRACKING_*` settings are history from the broadcast pipeline.

This model is **not** in the repo and was never committed — it must be trained.
All the tooling exists; this doc is the reproducible workflow.

## v4 — complete frames only (30 September 2026) — DEPLOYED ON PHONES

The v2/v3 training sets box only ~4 of the ~5.2 robots in a frame (`frc_v3_completed`:
3.97 boxes/frame), and every unboxed robot is taught as background — that is why recall
sat at 0.43–0.59 whatever the cutoff. v4 follows this document's own rule ("an incomplete
label is worse than no image"): **train only on frames that are already near-complete.**

- Data: `frc_v3_completed` frames with ≥5 boxes → `media/datasets/frc_v4_complete5`
  (798 train / 156 val, 5.70 boxes/frame), plus 45 missing robots confirmed by eye from
  112 strong proposals (the other 67 were stadium seats, the red field element, people).
  Proposals from v2/v3 were only ~40% robots even at high confidence, so nothing was
  auto-accepted. A ≥4-box variant (`frc_v4_complete4`) was trained too and lost.
- Training: Lambda Cloud, 1× A100 40 GB, YOLO11s and YOLO11n from COCO weights, 1280 px,
  time-capped (Ultralytics fills the cap with epochs). 3 h 23 min, ~$6.73 for all four runs.
- **Locked Einstein holdout (1280, eval only):**

| Model | mAP@0.5 | mAP@0.5:0.95 | P / R at its cutoff |
|---|---|---|---|
| v2 (previous) | 0.7189 | 0.5144 | 0.971 / 0.510 @0.15 |
| **v4_s (YOLO11s, complete5)** | **0.7791** | 0.5303 | 0.905 / 0.678 @0.35 |
| v4_s on complete4 | 0.7760 | 0.5362 | 0.943 / 0.609 @0.35 |
| **v4_n (YOLO11n, complete5)** | **0.7395** | 0.5040 | 0.906 / 0.604 @0.25 |
| v4_n on complete4 | 0.6954 ✗ | 0.4695 | — |

- On the labelled stands-view match (`ScoutingApp/scripts/benchmark-data`): v4_s finds
  3.95 robots/frame vs v2's 2.77–3.17 with identity as good (95.7% of hand labels, 2 mixed
  paths); a sample of boxes it adds over v2 was ~85% real robots, mostly at 0.8–0.9.
- Shipped per engine (`ScoutingApp/src/features/onDevice/modelArtifact.ts`): **v4_s on
  WebGPU at 0.35, v4_n on WASM at 0.25** (82 vs 243 ms/frame in WebKit; 10 MB). Full match:
  33 s in Chrome on the GPU, 55 s in WebKit on the CPU (M2).
- Weights: `media/models/frc_robot_detector_v4_{s,n}.pt` (not in git); ONNX exports
  (opset 19, dynamic, 896 base) are the tracked `ScoutingApp/public/models/*_v4_*.onnx`.

## Review update — 7 September 2026

The 10-epoch v3a experiment completed, including its final holdout evaluation.
A fresh comparison on all 224 locked images measured **v3 mAP@0.5 0.7610 vs v2
0.7257** at 1280 with square padding, NMS IoU .6 and max_det 50. At confidence .20,
recall was .6411 vs .5103 and precision .9141 vs .9785. mAP@0.5:.95 was essentially
unchanged (.5170 vs .5172). This is a detector-only comparison, not a production
tracking result. See [MODEL_REVIEW_2026-09-07.md](MODEL_REVIEW_2026-09-07.md).

**v3 is experimental, not approved for deployment.** Its labels failed review. The
experiment also changed the corpus, deduplication, splits and optimization, so it
does not isolate a labels-only effect. The old run used `optimizer=auto`; changing
the script afterward did not change that completed run.

Preparation scripts now reject protected holdout aliases, overlapping input/output
paths and stale output directories. Production training requires a matching accepted
human `audit/AUDIT.json`, the locked holdout, and finite mAP/recall gates. Experimental
training must explicitly use `--experiment-only`; it retains run checkpoints but does
not copy them to `--output-model`. Never reuse an existing model filename.

The gate runs CPU/batch 1/square padding/NMS .6/max_det 50; its default thresholds are
.7257 mAP and .5904 recall at 1280. Re-establish the baseline with the same protocol
when changing model size or evaluation settings. Passing this gate creates a local
candidate; deployment still requires the end-to-end test and explicit authorization.

---

## The v2 corpus was measurably broken — read this before retraining (6 Sep 2026)

Counting the v2 training set against the locked holdout turned up four things, and they
change the recipe below more than any architecture choice would:

| | Measured |
|---|---|
| Training label density | **3.31 boxes/frame** (54.7% of frames carry ≤3) |
| Locked holdout density | **5.19 boxes/frame** (98% carry 4–6) |
| Historical v2 detections at conf 0.05 | **3.41/frame** (old count sweep used different padding/NMS from AP validation) |
| Historical v2 detections at conf 0.01 | **5.41/frame** (count includes false positives; not a recall measurement) |

A REBUILT frame has six robots. Three training frames sampled at random from three
different events each carried **one box**. Unlabelled objects are not neutral: standard
detection loss reads an unboxed robot as background, so the model was explicitly taught
to suppress them. This is a plausible contributor to low recall, not a controlled
demonstration of the whole cause. Counts alone cannot distinguish missed robots from
false detections, and the corpus includes several footage types.

Three more corpus facts, all of which the file counts hide:

- **It is 4× smaller than it looks.** `frc_yolo_v2`'s 6,540 images are Roboflow
  augmentation copies of **1,635 unique frames**. Combined with the reviewed set the real
  corpus is **3,068 unique frames**, not 7,634.
- **Its split threw away half the data.** 814 unique base frames in train, 821 in val.
  (No base frame straddles the two, so there is no leakage — but nearly half the unique
  frames were never trained on.)
- **15% of it is simulator renders** (288 unique frames) and **185 frames are 2017
  footage**, despite this document's own v3 recipe ruling both out.

**So the v3 recipe is: fix the labels first, change the architecture last.** Tooling for
that now exists:

```bash
# 1. Deduplicate, group by camera view, quarantine the simulator frames, split 85/15.
PYTHONPATH=. python scripts/build_v3_dataset.py --out media/datasets/frc_v3_base

# 2. Nominate the missing boxes. Writes a review manifest; never auto-accepts.
PYTHONPATH=. python scripts/propose_missing_labels.py \
    --dataset media/datasets/frc_v3_base --split train val \
    --min-motion 10 --max-persistence 0.25 \
    --apply --out media/datasets/frc_v3_completed --report /tmp/completion.json

# 3. After completing the human audit, train a NEW candidate version.
PYTHONPATH=. python scripts/train_frc_yolo.py \
    --data media/datasets/frc_v3_reviewed/dataset.yaml \
    --base-model media/models/frc_robot_detector_v2.pt \
    --epochs 10 --imgsz 1280 --batch 4 --lr0 0.001 \
    --min-map50 0.7257 --min-recall 0.5904 \
    --output-model media/models/frc_robot_detector_v3_reviewed_candidate.pt

# 4. Compare honestly, at every resolution the model is actually served at.
PYTHONPATH=. python scripts/eval_detector.py \
    --model media/models/frc_robot_detector_v3.pt \
    --compare media/models/frc_robot_detector_v2.pt
```

Use `scripts/review_labels.py` to record keep/replace/skip decisions and apply them to
a fresh copy. The original and its review remain intact; byte hashes prevent stale
decisions. Corrections remain `needs_review` until the human quality gate is satisfied.
See `docs/LABELING_STANDARD.md` for the accepted audit JSON schema. For an explicitly
unaudited experiment, add `--experiment-only` and evaluate the resulting run checkpoint.

### The proposer's three vetoes, and what each costs

The detector at conf 0.01 already nominates about the right number of boxes; the problem
is the junk mixed in. Three filters, each measured against trusted labels rather than
tuned by eye:

| Veto | Removes | Measured cost |
|---|---|---|
| Cross-frame persistence (≥25% of a view's frames) | hubs, alliance walls, banners — the detector makes the *same* mistake at every scale, so agreement cannot catch them | **0 of 205** known robots |
| Motion energy vs. the view's median background | static field furniture | known robots median 35.2 vs. rejected median 5.2 — cleanly separated |
| Generic-COCO person veto | referees, drive teams, spectators | 2.6% of known robots |

Hand-counted on a magnified contact sheet, proposal error fell from **~14% to ~3–8%**.
These are preliminary historical samples, not a verified corpus-wide error rate.
The review found mirrored Roboflow variants interleaved within camera groups. A
temporal median is invalid across those variants. Cross-frame vetoes now require
orientation provenance and report disabled groups; unknown legacy groups are disabled.

---

## Status & honest benchmark (READ THIS)

| Model | Status | Honest benchmark (locked Einstein holdout) |
|---|---|---|
| `frc_robot_detector_v2.pt` | **Deployed (primary)** | **mAP@0.5 = 0.7172** |
| `frc_robot_detector_v1.pt` | Shipped in image as rollback | mAP@0.5 = 0.6991 |

- v2 was a low-LR fine-tune from v1 on the v2-mixed dataset (7,634 train / 339
  val); the aggressive 30-epoch candidate was correctly rejected at 0.6740 by
  the same holdout gate.
- **The historical deployment numbers are 0.7172 (v2) and 0.6991 (v1).** Both come from
  `media/datasets/frc_einstein_2026_holdout_reviewed_yolo` (224 reviewed
  images, Einstein 2026 — footage neither model trained on).
- **Do NOT report 0.98.** The ~0.98 figure (Roboflow-leaked split *and* the
  later "clean" in-distribution re-split) only measured performance on the
  narrow 42-scene training distribution. It is retired.
- **Deployment (bake path, hot-swap ready):** both v1 and v2 are whitelisted in
  `.dockerignore` and ship in the image. `VIDEO_TRACKING_YOLO_MODEL` selects
  which is primary — change the env var to roll back, no rebuild required.
  To move to object storage instead, set `VIDEO_TRACKING_YOLO_MODEL_URL`.
- **Serve it at 1280.** The model is trained and benchmarked at 1280 and the worker was
  running it at 960, which is silent: nothing errors and recall just drops. Same weights,
  identical square-padding validation settings: 960 → mAP@0.5 0.6358 / recall 0.4976,
  1280 → 0.7257 / 0.5904. It also decides whether a high-recall operating point exists at
  all — precision at recall 0.70 is 0.13 at 960 and 0.59 at 1280. Live `track()` uses
  rectangular padding and additional filters; do not present these as live metrics.
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
  --min-map50 0.7257 --min-recall 0.5904 \
  --output-model media/models/frc_robot_detector_new_candidate.pt
```

- `--device 0` for an NVIDIA GPU, `mps` on Apple Silicon, omit for CPU (slow).
- **`--min-map50` is a quality gate**: after training, the script validates the
  weights and **refuses to publish** if validation mAP@0.5 is below the
  threshold. A model that barely detects is worse than the honest DEGRADED
  flag, so don't lower this for production weights.
- This command requires an accepted human dataset audit. It creates a new candidate
  file; it does not replace the configured production model.

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

## After v3 — what is prepared and what it needs

Offline extraction and proposal scripts exist. Full semi-supervised training,
teacher/student distillation, architecture comparisons and phone validation remain
unimplemented or unvalidated; the commands below are starting points.

### Exploit the footage we never labelled

The labelled corpus covers 21 match videos. Every FRC event is streamed, so the
constraint has never been footage — it has been labels, and there is now a labeller.
`extract_eval_frames.py` turns arbitrary clips into a YOLO-layout frame set and
`propose_missing_labels.py` labels them:

```bash
# Any number of downloaded matches -> frames -> proposed labels -> a bigger corpus.
PYTHONPATH=. python scripts/extract_eval_frames.py --clips /path/to/clips \
    --out media/datasets/frc_selftrain --split train --per-clip 40
PYTHONPATH=. python scripts/propose_missing_labels.py \
    --dataset media/datasets/frc_selftrain --split train \
    --min-motion 10 --max-persistence 0.25 \
    --apply --out media/datasets/frc_selftrain_labeled
PYTHONPATH=. python scripts/audit_labels.py --dataset media/datasets/frc_selftrain_labeled --split train
```

This is offline self-training, which is the tractable form. Full teacher-student SSOD
(Efficient Teacher and friends) needs an EMA teacher, a dual-branch loop and a pseudo-label
assigner — a training-framework change ultralytics does not support natively, and not
something to ship untested. Do offline pseudo-labelling first and see whether it is
even needed.

**Cross-frame filters need grouped frames.** Persistence and motion both operate within
one camera view. `propose_missing_labels.py` derives the view from the filename, so keep
the `<videoid>_t<ms>` shape `extract_eval_frames.py` produces. A directory of unrelated
frames silently loses two of the three vetoes.

### Split the server model from the phone model

They have been sharing one set of compromises. The worker has no 333 ms frame budget and
no WebGPU operator table to respect; the PWA has both.

The browser student must stay YOLO-family. `onnxruntime-web`'s WebGPU backend has **no
`TopK` kernel** (checked against its operator table — `GridSample` is supported at opset
16–19, `TopK` is absent), so a DETR decoder's query selection splits the graph and falls
back to CPU silently. That is the same class of failure as the opset-22 export that ran
19× slower with no warning. Keep exports at **opset ≤ 19** and decode in JS.

YOLO26 is the interesting student — already present in `ultralytics 8.4.14`, including a
`yolo26-p2.yaml` stride-4 variant for small objects. It adds small-target-aware label
assignment, drops the DFL head that broke the int8 attempt, and exported with
`nms=False` it emits the same raw `1x5x8400` tensor `yoloDecode.ts` already parses:

```bash
PYTHONPATH=. python scripts/train_frc_yolo.py \
    --data media/datasets/frc_v3_completed/dataset.yaml \
    --base-model yolo26s.pt --epochs 60 --imgsz 1280 --batch 12 --device 0 \
    --min-map50 0.7172 --min-recall 0.5904 \
    --output-model media/models/frc_robot_detector_v4_yolo26s.pt
```

Do this **after** the label fix has been measured on its own, or the architecture's
contribution and the data's are inseparable. Then distil the server teacher into the
browser student with CWD or MGD (ordinary YOLO-to-YOLO recipes), and re-measure int8 on
the DFL-free head — the previous attempt failed on WebGPU speed (1151 ms vs 100 ms), and
DFL removal is specifically credited with easing quantisation. Re-measure; do not assume.

### Field calibration

AprilTag auto-calibration already exists for fixed event cameras
(`app/services/calibration/auto.py`). What does not exist is calibration for handheld
stands footage, where `cv2.aruco` finds **zero** field tags at that distance — proven on
real footage. That needs a learned field-keypoint model in the style of SoccerNet's
`nbjw_calib`, and therefore a keypoint-annotated dataset that does not exist yet. It is a
separate labelling project, not a training-recipe change.

---

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
