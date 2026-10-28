# Detector v3 review — 7 September 2026

**Recommendation: keep v2 deployed.** The completed v3 experiment improves detection
recall, but its label audit is not accepted and no real-match identity benchmark has
established that downstream team metrics improve. The implementation repairs below
are local; no deployment or additional training was started.

## Verified model result

Claude's 10-epoch training run finished. `results.csv` contains all ten epochs; the
local exported candidate and the run's `best.pt` are present. The run used
`optimizer=auto`; the later script fix did not change that run's effective optimizer.

A fresh, single-pass comparison evaluated all **224 images / 1,162 labeled boxes**
from the locked Einstein set, copying images and labels into a temporary directory.
The source hashes matched again after evaluation.

Protocol: Ultralytics 8.4.14, Torch 2.10.0, CPU, batch 1, 1280 pixels, square padding,
float32, NMS IoU .6, max_det 50, confidence floor .001, no TTA.

| Metric | v2 | v3 completed candidate |
|---|---:|---:|
| mAP@0.5 | .7257 | **.7610** |
| mAP@0.5:.95 | .5172 | .5170 |
| Precision at best F1 | .9444 | .8800 |
| Recall at best F1 | .5904 | .6652 |
| Maximum observed recall under this protocol | .7892 | .8571 |
| Precision at confidence .20 | .9785 | .9141 |
| Recall at confidence .20 | .5103 | .6411 |
| True positives at .20 | 593 | 745 |
| False positives at .20 | 13 | 70 |

The increase is useful, with a material precision cost. Stricter box localization
accuracy is essentially unchanged. This is one venue and one training run, not a
statistical demonstration of generalization. It also does not isolate a labels-only
effect: the corpus, deduplication, source split, simulator inclusion and fine-tuning
changed together.

These are **detector-only square-padding metrics**. The server calls `track()` with
rectangular inference padding, IoU .45 and max_det 20, followed by ROI/box/track
filters. Even Ultralytics `val(rect=True)` adds a half-stride pad that differs from
`predict()/track()`. Do not claim the benchmark reproduces a full production run.

Artifacts (local, ignored by git):

- `backend/media/model_review_20260907/evaluation_square1280.json`: full metrics,
  confidence sweeps, protocol and per-image hashes.
- v3 SHA-256: `532d52a2e25c0748abcd225b62d0c9fb68c5a06caed354dd6a731500b87bc0bb`.
- v2 SHA-256: `333a08b969f0ec120d1395ac6ef28ecc88fa01256b87105efce340cf21c5a05c`.
- Evaluation cohort hash: `6ffbf45d24d348dc9e7e847fdb8aeecea2a99fe33207d2b73eab284b637f3470`.

## Findings and repairs

1. **Incorrect identity joins.** The original merger chose tied candidates by track
   ID, could join co-observed singleton tracks, ran before OCR could veto conflicting
   teams, and hid fragmentation from retry selection. It now requires an unambiguous
   best continuation in both directions, rejects temporal overlap and scene changes,
   and carries per-fragment OCR evidence into the resulting canonical ID. Fuzzy OCR
   requires multiple sampled frames; duplicate text tokens in one frame do not count
   as independent temporal support. Contradictory teams cannot be bridged through an
   unidentified fragment. Tests cover the actual tracking-stage integration.

2. **Wrong point projected onto the floor.** The server projected bbox centroids
   through a floor homography. It now uses bbox bottom-center, retaining the legacy
   centroid fallback only when bbox coordinates are absent. Invalid projections stay
   missing. The analysis parameter hash records the new projection, OCR voter, and
   merge settings, so cached runs cannot silently stand in for changed behavior.
   Merging defaults off until real annotated tracks verify its false-join rate.

3. **Unsafe dataset/training paths and acceptance.** Basename checks could be bypassed
   through nested paths and symlinks; reruns mixed stale files into new datasets.
   Preparation now checks canonical containment, rejects overlapping outputs and
   nonempty destinations, preserves proposal provenance, and groups extracted
   `<clip>_t<ms>` frames by clip. Extraction no longer declares identical train/val
   directories. Training detects source/content leakage and fails before model load
   for missing/stale human audits or a missing holdout. NaN/invalid validation metrics
   cannot pass the gate. `--experiment-only` retains checkpoints without exporting
   deployable filenames; output model versions cannot be overwritten.

4. **Misleading evaluation.** The old count sweep used different padding/NMS from AP,
   ran five redundant inference passes and used a different ground-truth denominator.
   Its PR curve reported precision at recall beyond the model's reachable ceiling.
   One validation pass now supplies all counts; detections are rematched at each
   threshold, missing labels/orphan labels fail explicitly, unreachable recall is
   `null`, and model/data/protocol hashes are recorded. The locked inputs are copied,
   preventing library cache writes or JPEG repairs from modifying them.

5. **Review tooling could erase evidence.** Rerendering overwrote the filled audit,
   crop IDs lacked source mappings, and edge-clipped crops drew boxes in the wrong
   place. Audits now require fresh output and include `boxes/index.csv`. The new
   `review_labels.py` records keep/replace/skip actions and applies them to a separate
   copy only when source hashes still match. It never creates an accepted audit.

6. **Temporal label vetoes assumed consistent views that were not present.** The
   selected Roboflow variants alternate normal and mirrored orientations within the
   same group (for example `GX010282_0184`, `_0203`, `_0267`, `_0273`). A shared static
   median is invalid for these. Provenance now records augmented/unknown orientation;
   temporal vetoes are disabled and reported for unsupported groups. Motion/person
   filters are heuristics, not verified label truth.

7. **Tracker configuration guards asserted the wrong relationship.** High/new score
   gates above the detector floor are normal ByteTrack behavior; `match_thresh` is
   an association-cost gate. The arbitrary 1.6× confidence relationship was removed,
   and unreadable/invalid YAML no longer silently returns all-zero thresholds. The
   detector floor .20 still truncates ByteTrack's .08–.20 low-score band. Tuning that
   band needs annotated tracking evidence, so this review did not change it.

## Label work actually performed

The original `frc_v3_completed` holds **2,780 real frames**, not 3,068: the larger
figure includes the 288 quarantined simulator frames. The original data and Claude's
audit were preserved.

I inspected the six existing crop sheets (207 boxes), then selected suspicious frames
at original resolution. Seven plainly invalid original boxes were confirmed across
four frames: four in dark background above the 2017 field, two on grandstand seating,
and one bottom-of-frame broadcast/monitor sliver. This is a lower-bound finding, not
a completed label-recall audit. Claude's crop #82 is a real blue-bumpered robot next
to a person; deleting it would remove a valid label.

A new assisted copy contains **2,776 frames**. Four problematic frames were quarantined
for relabeling, one non-robot sliver was removed, and the valid warehouse frame was
preserved. No blanket removal of 2017/practice footage was performed. These are
assisted corrections and do **not** satisfy the human acceptance gate.

- `backend/media/model_review_20260907/label_corrections.json`: exact decisions and
  reasons, with source fingerprint.
- `backend/media/datasets/frc_v3_assisted_review_20260907/`: separate correction copy
  with `audit/AUDIT.json` explicitly marked `needs_review`.
- `backend/media/model_review_20260907/audit_train/`: 200 training frames, 767 labeled
  crops, 50 full-frame sheets, 20 crop sheets and a source index.
- `backend/media/model_review_20260907/verified_corrections/`: rendered corrected
  frames checked visually after applying the decisions.

No automated threshold or frame-density statistic can finish the remaining human
recall audit. Counts above six alone are not false-positive proof because the labeling
standard also includes real robots outside the playing field. The model-labeled
venue-shift set is not a benchmark for precision or recall until independently reviewed.

## Remaining work

Complete the human label pass, then run a controlled fine-tune holding corpus/split/
optimizer constant. Validate identity continuity and end-to-end findings on a real
annotated match before enabling fragment merging or replacing v2. Phone WebGPU,
thermals, a complete phone recording, new architectures, and teacher/student training
remain unverified; a Mac evaluation cannot substitute for them. No cloud GPU allocation,
model upload, infrastructure change, commit, push or deployment was performed.

## Verification

- Backend Ruff: passed. Full backend suite: **679 passed, 2 opt-in tests skipped,
  5 subtests passed**. Both skipped tests were then run explicitly and passed against
  disposable PostgreSQL 16 / Redis 7, using a copy of the current code without `.env`
  files or production data. PostgreSQL migrations reached `20260903_0013` (head).
  Concurrent on-device retries created one session/track set; Redis delivered room
  updates between two backend instances. Containers were stopped afterward.
- Frontend: **261 tests passed**, ESLint passed, TypeScript and production build passed.
  This does not constitute phone or browser-runtime validation.
- The v3 exported model matches the finished run's `best.pt` byte hash. Both model
  hashes, all locked image/label hashes, the original training-data fingerprint and
  the assisted copy's fingerprint were rechecked after execution.
- Corrected labels were rendered and visually inspected. The 200-frame audit output
  and crop index were opened and checked; its human verdict remains unfilled.
- `git diff --check` passed. All code changes remain local and uncommitted.
