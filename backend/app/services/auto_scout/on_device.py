from __future__ import annotations

# On-device track-production reference layer (Part B core math).
#
# This is the device-free, fully unit-testable core of the on-device match
# breakdown: it turns per-frame robot detections + a per-frame field homography
# into the same TrackPoint stream the shift-play engine already consumes, and it
# resolves track identity from shaky/angled bumper OCR as a closed-set vote over
# the match's 6 known team numbers.
#
# It is deliberately decoupled from any device, ONNX runtime, or OpenCV: the PWA
# will mirror this exact logic in-browser (WebGPU YOLO + AprilTag pose), and the
# server can run the identical math over real footage. The hard, device-bound
# pieces it does NOT do (in-browser YOLO inference, AprilTag detection + solvePnP)
# feed THIS layer their outputs:
#   per-frame AprilTag pose  ->  estimate_homography_ransac (ground-plane reduction)
#   YOLO + ByteTrack         ->  Detection(track_id, bbox, confidence)
#   bumper OCR               ->  OcrRead(text, confidence)
# Everything below is pure geometry / counting and is exact, so it can be locked
# down with synthetic tests before any model weights or handheld footage exist.

from collections import defaultdict
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from statistics import median
from typing import Sequence

import numpy as np

from app.services.auto_scout.shift_play import TrackPoint
from app.services.game_config import classify_point, load_game_config

# Minimum correspondences for a homography (a planar projective map has 8 DOF).
_MIN_CORRESPONDENCES = 4
# Largest gap between two samples of a track we still treat as continuous for speed.
_MAX_SPEED_DT_SEC = 1.0
# RANSAC defaults: an AprilTag corner is an inlier if it reprojects within this many
# metres; we try this many random 4-point fits looking for the largest consensus.
_RANSAC_INLIER_THRESHOLD_M = 0.15
_RANSAC_ITERATIONS = 200
# Top end-to-end robot speed (m/s) used to flag single-frame projection spikes.
_MAX_ROBOT_SPEED_MPS = 6.0


# ── Field geometry: per-frame homography + floor-contact projection ──────────


def estimate_homography(
    image_points: Sequence[tuple[float, float]],
    field_points: Sequence[tuple[float, float]],
) -> np.ndarray:
    # Normalized DLT homography mapping image pixels -> field metres. Inputs are
    # >=4 correspondences between detected ground-plane reference points (e.g. the
    # base of an AprilTag, or field-marking intersections recovered via solvePnP)
    # and their known field coordinates from game_config.field_layout.
    #
    # Normalization (centroid to origin, mean distance sqrt(2)) is what keeps the
    # SVD well-conditioned on shaky handheld frames where the raw pixel scale is
    # large; skipping it is the classic source of garbage homographies.
    if len(image_points) != len(field_points):
        raise ValueError("image_points and field_points must be the same length")
    if len(image_points) < _MIN_CORRESPONDENCES:
        raise ValueError(f"need >= {_MIN_CORRESPONDENCES} correspondences, got {len(image_points)}")

    src = np.asarray(image_points, dtype=np.float64)
    dst = np.asarray(field_points, dtype=np.float64)

    t_src = _normalization_matrix(src)
    t_dst = _normalization_matrix(dst)
    src_n = _apply_h(t_src, src)
    dst_n = _apply_h(t_dst, dst)

    rows = []
    for (x, y), (u, v) in zip(src_n, dst_n):
        rows.append([-x, -y, -1, 0, 0, 0, u * x, u * y, u])
        rows.append([0, 0, 0, -x, -y, -1, v * x, v * y, v])
    a = np.asarray(rows, dtype=np.float64)

    _, _, vt = np.linalg.svd(a)
    h_norm = vt[-1].reshape(3, 3)
    # Denormalize back into raw pixel/metre space.
    h = np.linalg.inv(t_dst) @ h_norm @ t_src
    if abs(h[2, 2]) < 1e-12:
        raise ValueError("degenerate homography (likely collinear correspondences)")
    return h / h[2, 2]


def _normalization_matrix(points: np.ndarray) -> np.ndarray:
    centroid = points.mean(axis=0)
    shifted = points - centroid
    mean_dist = float(np.sqrt((shifted**2).sum(axis=1)).mean())
    scale = float(np.sqrt(2) / mean_dist) if mean_dist > 1e-12 else 1.0
    return np.array(
        [[scale, 0, -scale * centroid[0]], [0, scale, -scale * centroid[1]], [0, 0, 1]],
        dtype=np.float64,
    )


def _apply_h(h: np.ndarray, points: np.ndarray) -> np.ndarray:
    homog = np.column_stack([points, np.ones(len(points))])
    projected = homog @ h.T
    return projected[:, :2] / projected[:, 2:3]


def project_point(h: np.ndarray, u: float, v: float) -> tuple[float, float]:
    # Apply an image->field homography to a single pixel coordinate.
    denom = h[2, 0] * u + h[2, 1] * v + h[2, 2]
    if abs(denom) < 1e-12:
        raise ValueError("point projects to infinity under this homography")
    x = (h[0, 0] * u + h[0, 1] * v + h[0, 2]) / denom
    y = (h[1, 0] * u + h[1, 1] * v + h[1, 2]) / denom
    return float(x), float(y)


def floor_contact_point(bbox: tuple[float, float, float, float]) -> tuple[float, float]:
    # The robot touches the field plane at the bottom-centre of its bbox, not the
    # centroid. Projecting bottom-centre is what makes an oblique handheld view map
    # to the right field cell; the centroid floats above the plane and drifts.
    x1, _y1, x2, y2 = bbox
    return ((x1 + x2) / 2.0, y2)


def bbox_to_field(h: np.ndarray, bbox: tuple[float, float, float, float]) -> tuple[float, float]:
    cx, by = floor_contact_point(bbox)
    return project_point(h, cx, by)


def reprojection_rmse(
    h: np.ndarray,
    image_points: Sequence[tuple[float, float]],
    field_points: Sequence[tuple[float, float]],
) -> float:
    # RMS field-space error of the homography over its correspondences. The caller uses
    # this as a per-frame pose-quality gate: when it exceeds tolerance (shake / no clear
    # tag) the caller passes homography=None for that frame, and produce_track_points
    # then carries the last good pose (see max_pose_staleness_sec).
    if not image_points:
        return float("inf")
    errors = []
    for (u, v), (fx, fy) in zip(image_points, field_points):
        px, py = project_point(h, u, v)
        errors.append((px - fx) ** 2 + (py - fy) ** 2)
    return float(np.sqrt(np.mean(errors)))


def _point_errors(
    h: np.ndarray,
    image_points: Sequence[tuple[float, float]],
    field_points: Sequence[tuple[float, float]],
) -> np.ndarray:
    errors = np.empty(len(image_points))
    for i, ((u, v), (fx, fy)) in enumerate(zip(image_points, field_points)):
        try:
            px, py = project_point(h, u, v)
        except ValueError:
            errors[i] = np.inf
            continue
        errors[i] = float(np.hypot(px - fx, py - fy))
    return errors


def estimate_homography_ransac(
    image_points: Sequence[tuple[float, float]],
    field_points: Sequence[tuple[float, float]],
    *,
    threshold_m: float = _RANSAC_INLIER_THRESHOLD_M,
    iterations: int = _RANSAC_ITERATIONS,
    seed: int | None = None,
) -> tuple[np.ndarray, list[int]]:
    # Robust homography for real footage: AprilTag detection hands us occasional bad
    # corners (motion blur, partial occlusion, a misread tag), and a single one wrecks
    # a plain DLT fit. RANSAC samples 4 correspondences, fits, counts how many of the
    # rest reproject within threshold_m, keeps the largest consensus, then refits on
    # the inliers only — so outliers are dropped automatically. Returns (H, inliers).
    if len(image_points) != len(field_points):
        raise ValueError("image_points and field_points must be the same length")
    n = len(image_points)
    if n < _MIN_CORRESPONDENCES:
        raise ValueError(f"need >= {_MIN_CORRESPONDENCES} correspondences, got {n}")
    # Too few to hold any out: nothing to vote with, so just fit them all.
    if n <= _MIN_CORRESPONDENCES:
        return estimate_homography(image_points, field_points), list(range(n))

    rng = np.random.default_rng(seed)
    best_inliers: list[int] = []
    for _ in range(max(1, int(iterations))):
        sample = rng.choice(n, _MIN_CORRESPONDENCES, replace=False)
        try:
            h_candidate = estimate_homography(
                [image_points[i] for i in sample], [field_points[i] for i in sample]
            )
        except (ValueError, np.linalg.LinAlgError):
            continue  # collinear / degenerate sample
        errors = _point_errors(h_candidate, image_points, field_points)
        inliers = [i for i in range(n) if errors[i] <= threshold_m]
        if len(inliers) > len(best_inliers):
            best_inliers = inliers
            if len(best_inliers) == n:
                break

    if len(best_inliers) < _MIN_CORRESPONDENCES:
        # No usable consensus (mostly noise): fall back to a best-effort fit over all.
        return estimate_homography(image_points, field_points), list(range(n))
    refit = estimate_homography(
        [image_points[i] for i in best_inliers], [field_points[i] for i in best_inliers]
    )
    return refit, best_inliers


# ── One-time manual calibration + optical-flow pose carry ────────────────────
#
# Real-footage testing (2026-06-20) showed the field's AprilTags are unresolvable
# from stands distance, so per-frame tag pose recovery is not viable there. The
# workable scheme is instead: tap the 4 field corners ONCE to fix a base pose, then
# carry it frame-to-frame with optical-flow stabilization (the cv2 side lives in
# on_device_cv.py to keep this module OpenCV-free / browser-mirrorable).


@dataclass(slots=True)
class Calibration:
    homography: np.ndarray  # image px -> field metres
    rmse_m: float           # reprojection error over the taps used (UI can reject if high)
    inliers: list[int]      # which taps were kept (RANSAC drops a sloppy one)


def field_reference_corners() -> list[tuple[float, float]]:
    # The four field-floor corners (metres) the 4-tap calibration maps taps onto, in
    # the order a UI should prompt for: (0,0), (L,0), (L,W), (0,W) per game_config dims.
    f = load_game_config().field
    length_m, width_m = float(f.length_m), float(f.width_m)
    return [(0.0, 0.0), (length_m, 0.0), (length_m, width_m), (0.0, width_m)]


def calibrate_from_taps(
    image_points: Sequence[tuple[float, float]],
    field_points: Sequence[tuple[float, float]],
) -> Calibration:
    # One-time manual calibration: >=4 tapped image points + their known field coords
    # (e.g. field_reference_corners) -> base image->field homography. RANSAC when >4 taps
    # so a sloppy tap is dropped; rmse_m lets the UI reject a bad calibration and re-tap.
    if len(image_points) != len(field_points):
        raise ValueError("image_points and field_points must be the same length")
    if len(image_points) < _MIN_CORRESPONDENCES:
        raise ValueError(f"need >= {_MIN_CORRESPONDENCES} taps, got {len(image_points)}")
    if len(image_points) > _MIN_CORRESPONDENCES:
        homography, inliers = estimate_homography_ransac(image_points, field_points)
    else:
        homography = estimate_homography(image_points, field_points)
        inliers = list(range(len(image_points)))
    rmse = reprojection_rmse(
        homography, [image_points[i] for i in inliers], [field_points[i] for i in inliers]
    )
    return Calibration(homography=homography, rmse_m=rmse, inliers=inliers)


def carry_pose(h_image_to_field: np.ndarray, interframe_motion: np.ndarray) -> np.ndarray:
    # Advance an image->field homography by one frame of camera motion. interframe_motion
    # maps previous-frame pixels -> current-frame pixels (from optical flow). A static
    # field point F sits at p_{t-1}=H_{t-1}^{-1}F and at p_t=M_t p_{t-1}; requiring
    # H_t p_t = F gives H_t = H_{t-1} M_t^{-1}. So the calibration rides through camera
    # motion without ever re-detecting a tag.
    return np.asarray(h_image_to_field, dtype=np.float64) @ np.linalg.inv(
        np.asarray(interframe_motion, dtype=np.float64)
    )


# ── Track production: detections + per-frame homography -> TrackPoints ────────


@dataclass(slots=True)
class Detection:
    track_id: int
    bbox: tuple[float, float, float, float]  # x1, y1, x2, y2 in image pixels
    confidence: float = 1.0


@dataclass(slots=True)
class Frame:
    time_sec: float
    homography: np.ndarray | None  # image->field for THIS frame; None = pose lost
    detections: list[Detection] = field(default_factory=list)


def produce_track_points(
    frames: Sequence[Frame],
    *,
    min_detection_confidence: float = 0.0,
    max_pose_staleness_sec: float | None = 0.5,
    max_speed_mps: float | None = _MAX_ROBOT_SPEED_MPS,
    smooth_window: int = 3,
) -> dict[int, list[TrackPoint]]:
    # Convert per-frame detections into per-track field-coordinate TrackPoints:
    # floor-contact -> project via the frame's homography -> zone-tag, then clean the
    # path before it reaches shift_play. Output feeds the shift-play engine directly.
    #
    # Detections below min_detection_confidence are dropped first (weak YOLO boxes are
    # mostly false positives). Then three robustness layers for shaky handheld footage:
    #  - pose fallback: a frame whose pose was lost (homography None) reuses the last
    #    good homography while it is at most max_pose_staleness_sec stale, so a few
    #    blind frames (no tag visible / motion blur) don't punch holes in tracks.
    #  - spike rejection: a single-frame projection that implies impossible speed and
    #    snaps back is dropped (max_speed_mps; None disables).
    #  - median smoothing: a small window damps per-frame jitter (smooth_window<3 off).
    by_track: dict[int, list[TrackPoint]] = defaultdict(list)
    last_h: np.ndarray | None = None
    last_h_time: float | None = None
    for frame in sorted(frames, key=lambda f: f.time_sec):
        h = frame.homography
        if h is None:
            if (
                max_pose_staleness_sec is not None
                and last_h is not None
                and last_h_time is not None
                and (frame.time_sec - last_h_time) <= max_pose_staleness_sec
            ):
                h = last_h  # carry the last good pose through the blind frame
            else:
                continue
        else:
            last_h = h
            last_h_time = float(frame.time_sec)
        for det in frame.detections:
            if det.confidence < min_detection_confidence:
                continue
            try:
                fx, fy = bbox_to_field(h, det.bbox)
            except ValueError:
                continue
            zone = classify_point(fx, fy)
            by_track[det.track_id].append(
                TrackPoint(
                    time_sec=float(frame.time_sec),
                    field_x=fx,
                    field_y=fy,
                    zone_key=zone.key if zone else None,
                    speed_mps=None,
                )
            )

    out: dict[int, list[TrackPoint]] = {}
    for track_id, points in by_track.items():
        points.sort(key=lambda p: p.time_sec)
        if max_speed_mps is not None:
            points = _reject_velocity_spikes(points, max_speed_mps)
        if smooth_window >= 3:
            _median_smooth(points, smooth_window)
        _fill_speeds(points)
        out[track_id] = points
    return out


def _track_distance(a: TrackPoint, b: TrackPoint) -> float | None:
    if None in (a.field_x, a.field_y, b.field_x, b.field_y):
        return None
    return float(np.hypot(b.field_x - a.field_x, b.field_y - a.field_y))


def _reject_velocity_spikes(points: list[TrackPoint], max_speed_mps: float) -> list[TrackPoint]:
    # Drop a point only when it jumps away from BOTH neighbours faster than a robot can
    # move yet its neighbours remain mutually reachable — i.e. a one-frame there-and-back
    # projection glitch. A genuine sustained move (enter a zone and stay) is preserved.
    if len(points) < 3:
        return points
    kept = [points[0]]
    for i in range(1, len(points) - 1):
        prev, curr, nxt = kept[-1], points[i], points[i + 1]
        dt_in = curr.time_sec - prev.time_sec
        dt_out = nxt.time_sec - curr.time_sec
        dt_span = nxt.time_sec - prev.time_sec
        d_in = _track_distance(prev, curr)
        d_out = _track_distance(curr, nxt)
        d_span = _track_distance(prev, nxt)
        if (
            None not in (d_in, d_out, d_span)
            and dt_in > 0
            and dt_out > 0
            and dt_span > 0
            and d_in / dt_in > max_speed_mps
            and d_out / dt_out > max_speed_mps
            and d_span / dt_span <= max_speed_mps
        ):
            continue  # spike: skip curr, keep prev as the anchor
        kept.append(curr)
    kept.append(points[-1])
    return kept


def _median_smooth(points: list[TrackPoint], window: int) -> None:
    # In-place median filter on each point's field position to damp per-frame jitter,
    # re-tagging the zone from the smoothed position. Median (not mean) keeps zone-step
    # edges crisp and is unmoved by a lone bad sample. Coordinates are snapshotted first.
    if window < 3 or len(points) < 3:
        return
    xs = [p.field_x for p in points]
    ys = [p.field_y for p in points]
    half = window // 2
    for i, point in enumerate(points):
        lo, hi = max(0, i - half), min(len(points), i + half + 1)
        wx = [xs[j] for j in range(lo, hi) if xs[j] is not None]
        wy = [ys[j] for j in range(lo, hi) if ys[j] is not None]
        if not wx or not wy:
            continue
        point.field_x = float(median(wx))
        point.field_y = float(median(wy))
        zone = classify_point(point.field_x, point.field_y)
        point.zone_key = zone.key if zone else None


def _fill_speeds(points: list[TrackPoint]) -> None:
    points.sort(key=lambda p: p.time_sec)
    for i in range(1, len(points)):
        prev, curr = points[i - 1], points[i]
        dt = curr.time_sec - prev.time_sec
        if dt <= 0 or dt > _MAX_SPEED_DT_SEC:
            continue
        if None in (prev.field_x, prev.field_y, curr.field_x, curr.field_y):
            continue
        dist = float(np.hypot(curr.field_x - prev.field_x, curr.field_y - prev.field_y))
        curr.speed_mps = dist / dt


def assemble_points_by_team(
    track_points: dict[int, list[TrackPoint]],
    identities: dict[int, str],
) -> dict[str, list[TrackPoint]]:
    # Relabel resolved tracks by team_key (merging multiple track fragments of the
    # same robot) so the result drops straight into analyze_match_shift_play. Tracks
    # with no resolved identity are dropped (the UI surfaces them for tap-ID).
    by_team: dict[str, list[TrackPoint]] = defaultdict(list)
    for track_id, points in track_points.items():
        team_key = identities.get(track_id)
        if not team_key:
            continue
        by_team[str(team_key).strip().lower()].extend(points)
    for points in by_team.values():
        points.sort(key=lambda p: p.time_sec)
    return dict(by_team)


# ── Identity: closed-set-of-6 bumper-OCR temporal voting ─────────────────────


@dataclass(slots=True)
class OcrRead:
    text: str  # raw per-frame OCR string (may be partial / garbled)
    confidence: float = 1.0


@dataclass(slots=True)
class IdentityVote:
    team_key: str | None  # winning team_key, or None when unresolved (tap-ID fallback)
    confidence: float
    resolved: bool
    scores: dict[str, float]  # per-candidate accumulated vote share, 0..1


# A read must resemble a candidate at least this much to count as a vote for it
# (rejects pure noise so a garbage frame doesn't pollute the closed set).
_MIN_READ_SIMILARITY = 0.5
# The winner must hold at least this share of the vote AND beat the runner-up by
# this margin to auto-resolve; otherwise we defer to tap-ID rather than guess.
_MIN_VOTE_SHARE = 0.45
_MIN_WINNER_MARGIN = 0.15


def _digits(text: str) -> str:
    return "".join(ch for ch in str(text) if ch.isdigit())


def vote_track_identity(
    reads: Sequence[OcrRead],
    candidate_team_keys: Sequence[str],
) -> IdentityVote:
    # Resolve a track to one of the match's 6 known teams by temporal voting. This is
    # the trick that makes shaky/angled/distant bumper OCR tractable: never free-OCR,
    # only score each per-frame read against the closed set of candidates, weight by
    # OCR confidence, and aggregate over the whole track (one track = one robot).
    candidates = [str(c).strip().lower() for c in candidate_team_keys if str(c).strip()]
    scores: dict[str, float] = {c: 0.0 for c in candidates}
    if not candidates:
        return IdentityVote(team_key=None, confidence=0.0, resolved=False, scores={})

    # Map "frc1234" / "1234" candidates to their bare digit form for matching.
    digit_form = {c: _digits(c) for c in candidates}

    for read in reads:
        read_digits = _digits(read.text)
        if not read_digits:
            continue
        weight = max(0.0, float(read.confidence))
        if weight <= 0.0:
            continue
        for cand in candidates:
            cand_digits = digit_form[cand] or cand
            similarity = SequenceMatcher(None, read_digits, cand_digits).ratio()
            if similarity >= _MIN_READ_SIMILARITY:
                scores[cand] += similarity * weight

    total = sum(scores.values())
    if total <= 0.0:
        return IdentityVote(team_key=None, confidence=0.0, resolved=False, scores=scores)

    shares = {c: s / total for c, s in scores.items()}
    ranked = sorted(shares.items(), key=lambda kv: kv[1], reverse=True)
    winner, winner_share = ranked[0]
    runner_share = ranked[1][1] if len(ranked) > 1 else 0.0
    resolved = winner_share >= _MIN_VOTE_SHARE and (winner_share - runner_share) >= _MIN_WINNER_MARGIN
    return IdentityVote(
        team_key=winner if resolved else None,
        confidence=round(winner_share, 4),
        resolved=resolved,
        scores={c: round(v, 4) for c, v in shares.items()},
    )
