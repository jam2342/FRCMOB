# Vision Service

This service handles all computer vision processing for match video. It's responsible for extracting video clips, detecting and tracking robots frame by frame, reading team numbers from bumper images, and analyzing robot trajectories — everything that turns a raw video file into structured spatial data.

## What It Does

Most of the useful scouting signals in this system ultimately come from video. This service is what makes that possible. It runs object detection and tracking on match footage, converts pixel coordinates to field coordinates using the calibrated homography, assigns robots to field zones, and produces `RobotTrack` records that downstream services (ratings, analysis) use to compute metrics.

## Files

**`video_extraction.py`**
Handles video acquisition. Can extract clips from YouTube or Twitch livestreams, cutting out the relevant portion of a match. Also handles frame extraction from stored video files for processing.

**`tracking_detection.py`**
The core detection and tracking pipeline.

1. Loads the FRC-tuned YOLO model (default: `media/models/frc_robot_detector_v2.pt`) and runs it on each video frame to detect robots. Generic COCO models (`yolo11n.pt`, …) are only a degraded fallback — a run that falls back to them is flagged `model_degraded: true` because generic models barely detect competition robots.
2. Passes detections into ByteTrack for multi-object tracking — associating detections across frames into continuous robot tracks. Max gap between detections: 2 frames. Match distance threshold: 110 pixels.
3. Applies the calibration homography to convert pixel positions to field coordinates.
4. Assigns each robot to a field zone at each frame.
5. Outputs `RobotTrack` records: `(x, y, speed, zone, confidence)` per frame per robot.

If tracking fails for a robot, a motion-based fallback is used to estimate its position.

**`track_analysis.py`**
Post-processing on the raw tracks. Computes aggregate metrics from `RobotTrack` data:

- Zone dwell times (how long a robot spent in each zone)
- Disabled period detection (robot was stationary for an extended period)
- Cycle reconstruction (inferred game piece cycles from zone transition patterns)
- Speed and movement profiles

These metrics feed directly into ratings and auto scout feature vectors.

**`bumper_ocr.py`**
Reads team numbers from robot bumper images using OCR. Used to identify which robot belongs to which team in the tracking output, especially when tracking IDs need to be mapped to actual team numbers.

**`perimeter_resolver.py`**
Detects which type of field perimeter is in use — Andymark vs. welded frame construction. This affects homography calibration parameters and is resolved automatically from video frames.

**`model_provisioning.py`**
Ensures the FRC detector weights are present at startup. Models aren't in git (`media/` is git-ignored), so this fetches them from object storage via `VIDEO_TRACKING_YOLO_MODEL_URL` when they're not already baked into the image, and verifies the configured `VIDEO_TRACKING_YOLO_MODEL` path exists before analysis runs.

## How It Runs

1. A match video is acquired — either from a live stream clip or stored footage.
2. Frames are extracted at the configured rate.
3. YOLO runs detection on each frame. ByteTrack associates detections across frames.
4. The calibrated homography transforms pixel coordinates to field coordinates.
5. Zone assignments are computed for each robot at each frame.
6. `track_analysis.py` aggregates the raw tracks into metrics (dwell times, cycles, disabled periods).
7. Bumper OCR resolves team numbers where needed.
8. `RobotTrack` records and derived metrics are written to the database.

## Key Parameters

| Parameter | Value |
|---|---|
| Default YOLO model | `media/models/frc_robot_detector_v2.pt` (generic COCO = degraded fallback) |
| ByteTrack max frame gap | 2 frames |
| ByteTrack match distance | 110 pixels |

## Dependencies

- OpenCV — frame extraction, image processing, AprilTag detection
- YOLO — robot detection model
- ByteTrack — multi-object tracking
- `calibration` service — homography matrix for coordinate transforms
- `RobotTrack` ORM model — output storage
- numpy — spatial math and coordinate transforms
