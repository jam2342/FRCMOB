# Calibration Service

This service handles two distinct but related calibration problems: mapping video coordinates to real field positions (homography), and calibrating how scoring rates are interpreted relative to a team's throughput (fuel rate calibration).

Both are foundational — homography is required before any spatial analysis can be done, and fuel rate calibration keeps scoring estimates grounded in reality as conditions change event to event.

## What It Does

### Field Calibration (Homography)

Robots are tracked in pixel space from a camera feed. To reason about where a robot actually is on the field, those pixel coordinates need to be converted to real-world field coordinates. This is done with a homography matrix — a 3×3 transform that maps from image space to field space.

The calibration is done automatically using AprilTag fiducials placed around the field. The service detects these tags in video frames, matches their positions to known field coordinates, and solves for the best-fit homography matrix using RANSAC.

### Fuel Rate Calibration

Scoring rate estimates drift depending on the quality of video analysis, field conditions, and match characteristics. This calibration fits a linear model — `scoring_rate = intercept + slope × throughput_score` — that keeps predicted scoring outputs in sync with actual observed data.

## Files

**`auto.py`**
Handles automatic field calibration from video. Samples frames at strategic points in the video, runs AprilTag detection on each frame, collects image-to-field coordinate correspondences, and solves for the best homography.

**`homography.py`**
The math layer. Computes the 3×3 homography matrix from a set of point correspondences. Uses RANSAC to handle noisy or partially occluded tag detections. Validates the result by checking the inlier ratio and reprojection RMSE — candidates with too many outliers or high reprojection error are discarded.

**`fuel_rate.py`**
Fits linear regression models for scoring rate calibration. Pulls recent `TeamMatchFinding` and `EventTeamRating` records, fits separate models for teleop and auto phases, and caches the result. If the database is unavailable, stale cached values are served as a fallback.

## How It Runs

**Video Calibration:**
1. Sample frames from the match video at strategic timestamps.
2. Detect AprilTag centers in each frame using OpenCV (DICT_APRILTAG_36h11).
3. Build a set of image ↔ field coordinate pairs.
4. Run RANSAC homography fitting — at least 4 point correspondences required.
5. Evaluate candidates by inlier count and reprojection RMSE.
6. Accept the best candidate if it meets quality thresholds.
7. Store the resulting `FieldCalibration` record for use by the vision service.

**Fuel Rate Calibration:**
1. Load recent match findings and event ratings.
2. Fit two linear models: one for teleop, one for auto.
3. Clamp slopes to valid ranges (teleop: 0.2–2.2, auto: 0.02–0.9) to prevent runaway predictions.
4. Cache the result with a TTL; serve stale values if the DB is unreachable.

## Key Parameters

| Parameter | Value |
|---|---|
| Min tag correspondences | 4 |
| RANSAC reprojection threshold | Pixel-based (configurable) |
| Teleop slope range | 0.2 – 2.2 |
| Auto slope range | 0.02 – 0.9 |

## Dependencies

- OpenCV — AprilTag detection and homography computation
- `game_config` — known field layout and AprilTag positions
- `TeamMatchFinding`, `EventTeamRating` — data sources for fuel rate calibration
