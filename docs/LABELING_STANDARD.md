# Labelling standard — FRC robot detection

One class: `0 = robot`. YOLO detection format, normalised `0 cx cy w h`.

The v2 training set has incomplete and incorrect labels. Label quality is a plausible
contributor to detector error; its contribution relative to architecture, resolution,
domain shift and optimization has not been isolated by a controlled experiment.

---

## The measurement that forced this document

Taken 6 Sep 2026 against `media/datasets/frc_einstein_2026_holdout_reviewed_yolo`:

| | Boxes / frame | Frames with ≤3 boxes |
|---|---|---|
| v2 training set (7,634 frames) | **3.31** | 54.7% |
| Locked Einstein holdout (224 frames) | **5.19** | 1.8% |

A REBUILT match has six robots on the field. The holdout labels essentially all of
them; the training set labels about half. Three training frames sampled at random from
three different events each carried **one box** on a frame containing six robots.

Unlabelled objects are not neutral. Standard detection loss reads every unboxed region
as background, so an unboxed robot is an instruction to suppress that detection. The
historical confidence sweep gave **3.41 detections/frame at conf 0.05** against a
training density of **3.31**, and **5.41 at conf 0.01** against a truth of **5.19**.
That sweep used different padding and NMS from the AP evaluation. Similar counts do
not prove which objects were detected: false positives can replace missed robots.
Use the corrected evaluator's matched precision/recall and recorded protocol.

Consequence: precision 0.94, recall 0.59 at 1280 px. The detector is nearly always right
when it speaks, and stays quiet about four robots in ten.

**So: an incomplete label is worse than no image at all.** Prefer 500 exhaustively
labelled frames to 5,000 half-labelled ones. This is also the finding of the
positive-unlabelled detection literature — *"having fewer images that are more
thoroughly annotated is preferable to a larger number of images with less thorough
labels."*

---

## The rule

> **Box every robot you can see. If you can tell it is a robot, it gets a box.**

That is the whole standard. Everything below is clarification, not exception.

### Box it

- **Every robot on the playing field**, all six, every frame.
- **Partially occluded robots** — behind another robot, behind a field element, behind a
  referee, behind the hub. Box the full extent you believe the robot occupies, including
  the hidden part, as long as you can tell it is a robot.
- **Robots cut off by the frame edge.** Box the visible portion. Clamp to the image.
- **Robots outside the field** — in the queue, at the alliance wall, on a cart. The
  holdout does this (frame `2kJRSXaYX04_frame_007` boxes a robot at the far right, off
  the playing surface), so training must too.
- **Motion-blurred robots**, as long as they are identifiable as a robot.
- **Distant / small robots.** The median robot is 61 × 43 px in a 1280 × 720 broadcast
  frame. Small is normal here, not a reason to skip.
- **Disabled or tipped robots.** Still a robot.

### Do not box it

- **People** — drive teams, referees, field staff, spectators. This is the single most
  common false positive the automated proposer produces, so it is worth stating.
- **Field elements** — hubs, alliance walls, loading depots, tower structures, carts,
  cable protectors. The hubs in particular are robot-shaped and robot-coloured; they are
  not robots.
- **Game pieces** (fuel), scoring displays, jumbotron / broadcast overlay content.
- **Robots visible only on a screen inside the frame** (a jumbotron replay, a monitor at
  the alliance station). Real robots only.
- Anything you cannot confidently identify as a robot. Leave it and flag the frame
  rather than guess — but note that "I am not sure" should be rare; when it is common,
  the frame is probably too low-quality to be useful.

### Box geometry

- Tight to the robot's visible silhouette, **including bumpers**, excluding shadow.
- Include raised mechanisms (arms, elevators, hoppers) — they are part of the robot and
  the tracker follows the whole object.
- Where a robot is occluded, box the **inferred full extent**, not just the visible
  sliver. Consistency matters more than millimetre accuracy: the whole set must agree
  with itself.

### Frames to skip entirely

Delete the frame rather than label it partially:

- Replays, slow motion, and score-graphic cutaways.
- Close-up or pit shots where the field is not visible.
- Frames where the field is so obscured you cannot tell how many robots are present.

A skipped frame costs nothing. A half-labelled frame costs recall.

---

## Quality gate

A dataset is only accepted into training when a random **200-frame audit** clears:

| Check | Target |
|---|---|
| Label recall vs. a careful manual count | **≥ 95%** |
| Mean boxes / frame | within **±0.3** of the reference set for that footage type |
| False positives (people, field elements) | **≤ 2%** of boxes |

Record the audit result in the dataset directory as `AUDIT.md`. A dataset without one has
not been checked.

For production-candidate training, also record `audit/AUDIT.json`:

```json
{
  "version": 1,
  "status": "accepted",
  "dataset_sha256": "<scripts.dataset_safety.dataset_fingerprint result>",
  "splits": ["train", "val"],
  "reviewer": "<person who performed the audit>",
  "reviewer_kind": "human",
  "reviewed_frames": 200,
  "label_recall": 0.95,
  "false_positive_rate": 0.02,
  "reference_density": 5.19
}
```

Values above illustrate the acceptance boundaries, not a completed audit. Supply the
actual measurements and an appropriate density reference for the footage mix. Audit
at least 200 frames (all frames if fewer exist), spanning train and val. The fingerprint
binds image paths, image bytes and label bytes; changing any invalidates acceptance.
`review_labels.py` writes only `needs_review` artifacts, including for human correction
sessions. A correction pass alone does not establish recall or dataset acceptance.

For broadcast field-wide footage the reference density is the Einstein holdout's
**5.19 boxes/frame**. Cropped or partial-field footage will legitimately differ — state
the reference you used.

---

## Tooling

`backend/scripts/propose_missing_labels.py` does the first pass: it runs the current
detector over several scales with TTA, keeps only boxes several views agree on, then
applies three vetoes measured against trusted labels —

| Veto | What it removes | Cost, measured |
|---|---|---|
| **Cross-frame persistence** | a box firing in the same place in ≥25% of one camera view's frames — hubs, walls, banners | 0 of 205 known robots |
| **Motion energy** | a box that matches the view's median background — static field furniture | separates cleanly: known robots median 35.2, rejected median 5.2 |
| **Person veto** | a box a generic COCO detector calls a person | 2.6% of known robots, vs ~14% proposal error rate |

**Give it many frames from few camera views, not few frames from many.** The persistence
veto works by noticing that a box keeps firing in the same place across one continuous
view, so it needs enough frames of that view to tell static furniture from a robot that
happened to pause. With 100+ frames per view it removed 0 of 205 known robots while
cutting the hub and wall detections; with 25 frames per view (the first venue-shift eval
build) it let through enough that 30% of frames carried 8-12 boxes for a six-robot field.
Prefer 100 frames from 6 clips over 25 frames from 24. This assumes a stable view and
consistent image orientation. The v3 review found randomly mirrored Roboflow variants
interleaved inside camera groups: their shared median background is invalid. The
proposer now disables temporal vetoes for unknown/augmented orientation groups and
reports them as `temporal_veto_disabled_groups`.

Counts above six are review flags, not proof of false positives: this standard also
labels real robots outside the playing field. Model-generated venue-shift labels are
not ground truth for either precision or recall until independently reviewed.

The output is a **review manifest**, not a finished dataset. Proposals are nominated,
never auto-accepted. Confirm-or-reject on a pre-drawn box is several times faster than
drawing from scratch, which is the entire point — but the human step is not optional.
The published result this loop is modelled on
([tiny surface-defect detection, 2026](https://www.mdpi.com/1424-8220/26/12/3912)) went
from mAP@0.5 0.2814 to 0.6320 and recall 0.1842 to 0.6220 *with* human confirmation in
the loop.

### Apply corrections without losing the source or the review

```bash
# From backend; use a new path for each audit render so earlier verdicts are preserved.
PYTHONPATH=. python scripts/audit_labels.py --dataset media/datasets/frc_v3_completed \
  --sample 200 --out media/label_review/audit_001
PYTHONPATH=. python scripts/review_labels.py --dataset media/datasets/frc_v3_completed \
  --sample 200 --out media/label_review/decisions.json
# Inspect original frames and boxes/index.csv. Edit action/reason/boxes in decisions.json.
PYTHONPATH=. python scripts/review_labels.py --dataset media/datasets/frc_v3_completed \
  --apply media/label_review/decisions.json --out media/datasets/frc_v3_corrected
```

Actions are `pending`, `keep`, `replace` (the full corrected YOLO box list), and `skip`.
Keep uncertain frames pending, or quarantine them for relabeling with a recorded reason.
The original dataset and old audit stay intact. Exported manifests include both splits;
the sheet renderer samples the selected split. Review at original resolution before
changing labels: Claude's crop #82 was a real blue-bumpered robot beside a person.

---

## Hard rules

- **Never label, split, or train on**
  `media/datasets/frc_einstein_2026_holdout_reviewed_yolo`. It is the only honest
  benchmark. `source_grouped_split.py`, `import_external_yolo_labels.py` and
  `propose_missing_labels.py` all refuse it by name.
- **Split by source video, never by filename.** Consecutive frames and augmented
  siblings must not straddle train/val. Use `scripts/source_grouped_split.py`.
- **Real footage only.** The v2 set contained 1,152 simulator renders (15%) despite
  `TRAINING.md` saying otherwise. Keep synthetic frames in a separate bucket and measure
  with and without them rather than assuming they help.
