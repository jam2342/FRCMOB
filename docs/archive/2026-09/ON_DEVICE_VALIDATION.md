# On-device validation evidence

Cleanup follow-up (2026-09-21): the default browser command reports an
`inference-smoke`, not match accuracy. Set `ONDEVICE_REQUIRE_FULL_MATCH=true`
together with `ONDEVICE_MATCH_VIDEO` and `ONDEVICE_CALIBRATION_IMAGE` to require
the workflow path. That path rejects zero tracks/points or missing submission;
the sync server is still mocked. Optional `ONDEVICE_TRACK_IDENTITIES` is a JSON
file mapping track IDs to team keys (for example `{"1":"frc118"}`). Without it,
team assignments are synthetic and explicitly labeled in the result. Neither
mode certifies server persistence or scouting accuracy. See
[the cleanup results](DEBLOAT_RESULTS_2026-09-21.md) for current verification.

Last updated: 2026-09-04

## Local environment and artifact

- Host: Apple Silicon (`arm64`), macOS 26.2.
- No full Xcode installation or `simctl` runtime is available, and installing one would
  not answer the open question anyway: the iOS Simulator runs arm64 code natively against
  the **Mac's** GPU via Metal, so its WebGPU numbers are Mac numbers, and it cannot
  reproduce the thermal behaviour of a passively cooled phone at all. Playwright's
  iPhone 15 profile is used for layout and touch, and is honest about being a proxy.
- Model: `frc_robot_detector_v2_896.onnx`, 38.0 MB fp32, imgsz 896, opset 19.
- SHA-256: `413300e783aad514526c8d13650ecd498f940ce3919fdb09e9f5be70cb764e51`.
- Superseded: `frc_robot_detector_v2.onnx` (imgsz 640, opset 12), SHA-256
  `babd760102444199075a59a9a5754d07bdc1c1a225ab4ee40ff7c2f853904430`. Same weights, half
  the accuracy — see the resolution section below.
- Repeatable command: `npm run validate:on-device-browser` from `ScoutingApp/`.
  Set `ONDEVICE_HEADLESS=false` for a real GPU adapter (see below).

## Browser runtime results

| Runtime | Adapter | Provider | Iterations | Median | p90 | fps | Drift |
|---|---|---|---:|---:|---:|---:|---:|
| Chromium headed, iPhone 15 profile | apple / metal-3 | WebGPU | 30 | 55.1 ms | 57.6 ms | 18.1 | 0% |
| Playwright WebKit, iPhone 15 profile | — (no `navigator.gpu`) | WASM | 10 | 562.5 ms | 576.0 ms | 1.78 | -1% |
| Chromium **headless**, iPhone 15 profile | software fallback | "WebGPU" | 30 | 8146.3 ms | 9629.4 ms | 0.12 | -3% |

The headless row is retained only as a warning. Headless Chromium resolves `navigator.gpu`
to a software adapter, so it benchmarks SwiftShader and lands ~148x slower than the real
GPU — and slower than plain WASM. Do not quote it as a WebGPU result. The harness now
reports `gpuAdapter` on every run so a software fallback can never be mistaken for
hardware again.

The headed number is a real Metal-3 adapter driving the real `onnxruntime-web` WebGPU
path: **55 ms/frame, 18 fps**, against a 2-5 fps target. For reference, native CoreML on
the same machine is 14.7 ms, so the browser WebGPU path costs roughly 3.7x native. This
is Mac silicon, not a phone, and it is still not a thermal measurement.

## Complete-match run — PASSES

Source: [2026 Championship Einstein Playoff Match 13](https://www.thebluealliance.com/match/2026cmptx_sf13m1),
182.23 seconds, 1280x720. A real field frame was four-corner calibrated and the match
start anchored at video timestamp 8.0 s.

| Measure | First passing run | After the sampler work |
|---|---|---|
| Wall clock | 184.99 s (1.015x real time) | **92.46 s (0.51x real time)** |
| Frames sampled | 495 | 495 of 495 target |
| Tracks produced | 14 | 13-14 across runs |
| Points synced | 55 | 51 |
| Inference | median 75 ms, p90 87 ms | median 54 ms (warm), WebGPU |
| Thermal drift | -1% | -1% (no throttling) |
| Backend quality score | 0.8375 | 0.8375 — review-eligible |

A 182.23 s match now breaks down in about half its own duration, with no loss of sampled
frames. Track count varies 13-14 run to run, which is tracker nondeterminism, not a
sampling effect: the frame count is identical.

### Making it twice as fast without losing data

At 1x the sampler was idle roughly 76% of the wall clock — 495 frames at ~55 ms is about
27 s of GPU work inside 185 s — because it walked a file already on disk in real time.
Two changes, both measured rather than assumed:

**Playback rate is earned, not assumed.** After six warm-up samples the sampler takes the
median per-frame cost and speeds playback up to whatever that cost can keep pace with,
spending at most half the inter-sample budget. A slow phone measures a high cost and stays
at 1x, so this cannot silently degrade a weak device.

**The cap is a presentation limit, not a compute one.** Inference alone would allow ~5x,
but the browser stops presenting a distinct frame for every interval past 2x. Measured on
a 20 s clip with the detector switched *off*, 2x kept 60/60 target frames and 3x kept
57/60. Those misses are not spread evenly — each one doubles the gap between kept frames —
and a full-match run at 3x dropped tracks from 14 to 10 because the tracker could not
associate across the gaps. 3x was 2.87x faster and quietly cost a quarter of the tracks;
it was rejected for that reason. Raise the cap only against a re-measured frame count
*and* track count.

**The interval was drifting.** The sampler asked for "one frame every interval since the
last frame I took". A presented frame essentially never lands exactly on a boundary, so
each sample re-anchored the schedule slightly late and the overshoot compounded: 484 of
495 samples, with the loss showing up as widened gaps that cost whole tracks rather than
an even thinning. Anchoring to a fixed grid restored the full 495 and the baseline track
count. This was worth more than the speed-up — it is a correctness fix that also applied
at 1x.

### Where the remaining time goes

92 s for 27 s of GPU work is still ~70% idle, and the 2x ceiling is the browser's frame
presentation. Removing it means decoding without playback — `WebCodecs` `VideoDecoder`
fed by an MP4 demuxer — which would make the run compute-bound at roughly 40 s. That is a
real rewrite and a new dependency, so it is deliberately not attempted here. Separately,
the 36 MB fp32 model is a heavy mobile download; int8 quantisation (~9-10 MB) is the
obvious win but changes the model and must clear the locked holdout first.

### Why the earliest attempts failed outright

The earlier attempts were recorded as "did not reach track assembly in 12 / 20 minutes"
and were attributed to sampling fps. That diagnosis was wrong. The real cause was a
blocking bug, since fixed:

Capture opens in **camera** mode, and entering that stage eagerly called `loadOpenCv()`.
OpenCV.js is an ~8 MB emscripten bundle injected as a classic `<script>`; compiling it
occupies the main thread. A scout who lands on the capture step and immediately picks
"Upload video" had already started a compile that nothing cancels. That compile starved
`onnxruntime`'s WebGPU session creation, so `createDetector` never returned and the run
produced no tracks at all. It also blocked the timers inside `loadOpenCv`, so its own
45 s load timeout could not fire to release it.

Bisected by stage, creating the detector took 736 ms at page load and 570 ms after setup,
but never completed after calibration — pinned at 100% of one core. Disabling
stabilisation alone returned it to 573 ms.

The fix holds the OpenCV load behind a short grace period that is cancelled when the
scout leaves camera mode, so passing through camera on the way to the upload path no
longer starts it. Regression tests cover all three cases, including that a scout who
stays on the camera path still gets stabilisation.

## Detector accuracy on device is not the number we quote

`0.7178` mAP@0.5 is the figure this project cites for the detector. It is measured at
**imgsz 1280**. The PWA runs the ONNX export at **640**. Those are very different models
in practice.

Measured on the locked Einstein holdout, square-letterboxed exactly as the browser feeds
it (`rect=False`):

| imgsz | mAP@0.5 | recall | precision | vs 1280 | WebGPU / frame |
|---:|---:|---:|---:|---:|---:|
| **640 (shipped on device)** | **0.3402** | 0.2347 | 0.7820 | **47%** | 53 ms |
| 768 | 0.5349 | 0.4150 | 0.8943 | 74% | — |
| 896 | 0.6058 | 0.4844 | 0.9066 | 84% | 102 ms |
| 1024 | 0.6472 | 0.5250 | 0.9342 | 90% | 153 ms |
| 1280 | 0.7189 | 0.5877 | 0.9408 | 100% | — |

So the on-device detector has been running at **47% of the accuracy the documentation
implies**, entirely because of input resolution. Robots are small in a wide field shot and
recall is what collapses: 0.588 -> 0.235.

The ONNX export itself is faithful — `.pt` at 640 with square padding scores 0.3402, the
same to four decimals as the ONNX. There is no export bug, only a resolution choice
nobody had measured.

**Shipped: 896.** It nearly doubles mAP (+78%) for +46 ms a frame. A 3 fps sample interval
allows 333 ms and optical flow now takes ~46 ms of it, so 896 lands at ~148 ms of the budget
and leaves room for a phone slower than this Mac. 1024 is defensible if the device can take
it; 1280 cannot fit the interval.

On a real Einstein field frame at 1280x720 the difference is not subtle: the 640 export
returns **zero detections**, the 896 export returns **two robots at 0.90 confidence**.

`scripts/export_on_device_model.py` produces the artifact and defaults to 896;
`detector.ts` points at `frc_robot_detector_v2_896.onnx`. The model is not git-tracked, so
production also needs the new file uploaded wherever `VITE_ONDEVICE_MODEL_URL` points.

### int8 quantisation — measured and rejected

int8 shrinks the download 3x and is unusable, because `onnxruntime-web`'s WebGPU backend
has no fast integer path.

| model | mAP@0.5 | size | WebGPU / frame |
|---|---:|---:|---:|
| 896 fp32 (**shipped**) | 0.6058 | 38.0 MB | 100 ms |
| 896 int8, QDQ | 0.6093 | 12.7 MB | 1151 ms |
| 896 int8, QOperator | — | 12.5 MB | 1640 ms |

Accuracy was never the problem — mixed int8 matched fp32 (0.6093 vs 0.6058, inside noise).
Speed was: 11-16x slower, far outside the 333 ms interval. A 3x smaller download does not
buy that.

Two things worth keeping if anyone retries this:

- **Whole-model int8 destroys this detector, and does it quietly.** Box regressions stay
  sane while every confidence score quantises to exactly `0.0000`, so the model loads,
  runs, and detects nothing — mAP 0.0, per-tensor *and* per-channel. Excluding the
  `model.23` head from quantisation is what brings it back.
- **Per-channel needs opset >= 13**, because it emits `DequantizeLinear` with an `axis`
  attribute.

### The opset trap

Ultralytics defaults to opset 22, and `onnxruntime-web`'s WebGPU backend has no fast
kernels for parts of it — it falls back silently. Same weights, same 640 input, on this
machine: **opset 12 -> 52 ms, opset 22 -> 1133 ms**, a 19x regression with near-identical
graphs (353 vs 355 nodes).

The cliff is not where it first looked. Opsets **12, 13, 15, 17 and 19 all run at ~53 ms**;
only 22 collapses. So the rule is "stay at or below 19", not "pin to 12" — which matters,
because per-channel quantisation needs at least 13. The export script uses 19.

`detector.ts` now reads its input size from the loaded model instead of hard-coding 640,
so swapping the artifact is all that is required to change resolution.

### Two things this pass also fixed

- `verify_holdout.py`, the monthly drift tripwire, had been **broken since the repo moved**:
  `dataset.yaml` carries an absolute `path:` pointing at `Desktop/FRCMOB/...` while the
  repo now lives at `Desktop/Projects/FRCMOB/...`, so every run died with "images not
  found". It now rewrites the path to wherever the holdout actually is, and validates with
  `rect=False` so it reports what the deployment actually gets.
- Holdout numbers here are quoted square-letterboxed. At 1280 that is 0.7189 against the
  0.7178 rectangular figure, so the headline baseline is unchanged.

## Acceptance decision

The threshold **stays at 0.80**. Scored with the real backend function, it separates the
cases the way it is meant to:

| Scenario | Score | Outcome |
|---|---:|---|
| Fixed-camera upload, verified calibration, manual ID (*measured*) | 0.8375 | accepted |
| Handheld, optical-flow pose holding, manual ID | 0.8775 | accepted |
| Handheld, optical flow losing 40% of frames | 0.7775 | forced review |
| Handheld, no stabilisation, perfect manual ID | 0.6900 | forced review |
| Fixed-camera upload, calibration not verified | 0.6625 | forced review |

The measured run clears by only 0.0375, so the bar leaves no slack for a sloppy
calibration or a weak identity pass — which is the intent. Legacy or unrecognised schemas
still score `0` and always require forced approval.

Human acceptance still does **not** authorise ratings or training use. Both paths remain
provenance-quarantined behind `real_phone_ground_truth_validation_pending`. Nothing
measured here is positional or identity ground truth: the calibration RMSE of a four-tap
fit is mathematically exact (6.2e-15 m in this run) and is not accuracy evidence, and
identity was tap-assigned rather than read from bumpers.

## Still required before ratings or training use

Physical-device release gates are maintained in `PROD_RUNBOOK.md`. Do not tune the
threshold down from these results. The next required evidence is a complete match on at
least two real phone performance tiers, recording GPU provider, fps, thermal drift,
position error against a known reference, identity precision, and timing error. Bumper
OCR remains unavailable on-device, so identity is tap-assigned by design.
