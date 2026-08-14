# Bumper-number OCR for robot identity hints.
#
# Extracted from jobs.py to reduce module size.
from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from app.core.config import settings
from app.db import models
from app.services.utils import _clamp, _round

def _team_number_from_team_key(team_key: str | None) -> int | None:
    if not team_key:
        return None
    match = re.match(r"^frc(\d+)$", str(team_key).strip().lower())
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None

def _normalize_ocr_digits(raw_text: str | None) -> list[int]:
    if not raw_text:
        return []
    cleaned = str(raw_text)
    # Common OCR confusions on FRC bumpers.
    cleaned = cleaned.replace("O", "0").replace("o", "0")
    cleaned = cleaned.replace("I", "1").replace("l", "1").replace("|", "1")
    cleaned = cleaned.replace("S", "5")
    candidates: list[int] = []
    for token in re.findall(r"\d{1,5}", cleaned):
        try:
            value = int(token)
        except ValueError:
            continue
        if 1 <= value <= 99999:
            candidates.append(value)
    return candidates

def _ensure_tesseract_available():
    try:
        import pytesseract  # type: ignore
    except Exception as exc:
        raise RuntimeError(
            "pytesseract is not installed. Install it to enable bumper OCR."
        ) from exc
    try:
        pytesseract.get_tesseract_version()
    except Exception as exc:
        raise RuntimeError(
            "Tesseract binary is not available. Install tesseract-ocr in the container."
        ) from exc

def _infer_track_bumper_numbers(
    *,
    sample_frames: list[Path],
    observations: list[dict],
    match_teams: list[models.MatchTeam],
) -> tuple[dict[int, dict], dict]:
    if not settings.video_tracking_enable_bumper_ocr:
        return {}, {"enabled": False, "reason": "disabled"}
    if not sample_frames or not observations:
        return {}, {"enabled": True, "reason": "no_frames_or_observations"}

    valid_team_numbers: dict[int, str] = {}
    for match_team in match_teams:
        team_number = _team_number_from_team_key(match_team.team_key)
        if team_number is not None:
            valid_team_numbers[team_number] = match_team.team_key
    if not valid_team_numbers:
        return {}, {"enabled": True, "reason": "no_match_team_numbers"}

    try:
        import cv2  # type: ignore
    except Exception as exc:
        return {}, {"enabled": True, "error": f"opencv_unavailable: {exc}"}

    try:
        import pytesseract  # type: ignore
        _ensure_tesseract_available()
    except Exception as exc:
        return {}, {"enabled": True, "error": str(exc)}

    grouped: dict[int, list[dict]] = defaultdict(list)
    for row in observations:
        grouped[int(row["track_id"])].append(row)

    max_samples_per_track = max(2, int(settings.video_tracking_bumper_ocr_max_samples_per_track))
    min_text_conf = _clamp(float(settings.video_tracking_bumper_ocr_min_text_conf), 0.05, 0.99)
    expand_ratio = _clamp(float(settings.video_tracking_bumper_ocr_expand_ratio), 0.0, 0.7)
    bottom_ratio = _clamp(float(settings.video_tracking_bumper_ocr_bottom_band_ratio), 0.15, 1.0)
    ocr_language = (settings.video_tracking_bumper_ocr_language or "eng").strip() or "eng"

    frame_cache: dict[int, object] = {}
    track_hints: dict[int, dict] = {}
    total_ocr_reads = 0
    total_hits = 0

    for track_id, rows in grouped.items():
        rows_by_frame = sorted(rows, key=lambda row: int(row.get("frame_index") or 0))
        n = len(rows_by_frame)
        bucket_size = max(1, n // max_samples_per_track)
        selected = [
            max(
                rows_by_frame[i : i + bucket_size],
                key=lambda row: float(row.get("confidence") or 0.0),
            )
            for i in range(0, min(n, max_samples_per_track * bucket_size), bucket_size)
        ][:max_samples_per_track]
        vote_scores: dict[int, float] = defaultdict(float)
        vote_counts: dict[int, int] = defaultdict(int)

        for row in selected:
            frame_index = int(row.get("frame_index") or 0)
            if frame_index < 0 or frame_index >= len(sample_frames):
                continue

            image = frame_cache.get(frame_index)
            if image is None:
                image = cv2.imread(str(sample_frames[frame_index]))
                frame_cache[frame_index] = image
            if image is None:
                continue

            frame_h, frame_w = image.shape[:2]
            x1 = int(max(0, min(frame_w - 1, float(row["bbox_x1"]))))
            y1 = int(max(0, min(frame_h - 1, float(row["bbox_y1"]))))
            x2 = int(max(0, min(frame_w, float(row["bbox_x2"]))))
            y2 = int(max(0, min(frame_h, float(row["bbox_y2"]))))
            if x2 <= x1 + 3 or y2 <= y1 + 3:
                continue

            box_w = x2 - x1
            box_h = y2 - y1
            pad_x = int(box_w * expand_ratio)
            pad_y = int(box_h * expand_ratio)
            roi_x1 = max(0, x1 - pad_x)
            roi_x2 = min(frame_w, x2 + pad_x)
            roi_y1 = max(0, y1 - pad_y)
            roi_y2 = min(frame_h, y2 + pad_y)
            if roi_x2 <= roi_x1 + 3 or roi_y2 <= roi_y1 + 3:
                continue

            roi = image[roi_y1:roi_y2, roi_x1:roi_x2]
            if roi is None or roi.size == 0:
                continue

            band_start = int(max(0, roi.shape[0] * (1.0 - bottom_ratio)))
            bumper_roi = roi[band_start:, :]
            if bumper_roi is None or bumper_roi.size == 0:
                continue

            gray = cv2.cvtColor(bumper_roi, cv2.COLOR_BGR2GRAY)
            gray = cv2.GaussianBlur(gray, (3, 3), 0)
            upscaled = cv2.resize(gray, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
            thresh = cv2.adaptiveThreshold(
                upscaled,
                255,
                cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                cv2.THRESH_BINARY,
                31,
                7,
            )
            ocr_input = thresh

            try:
                total_ocr_reads += 1
                ocr_result = pytesseract.image_to_data(
                    ocr_input,
                    lang=ocr_language,
                    config="--oem 1 --psm 7 -c tessedit_char_whitelist=0123456789",
                    output_type=pytesseract.Output.DICT,
                )
            except RuntimeError:
                continue

            detection_weight = _clamp(float(row.get("confidence") or 0.4), 0.1, 1.0)
            texts = ocr_result.get("text") if isinstance(ocr_result, dict) else None
            confs = ocr_result.get("conf") if isinstance(ocr_result, dict) else None
            if not isinstance(texts, list) or not isinstance(confs, list):
                continue
            for raw_text, raw_conf in zip(texts, confs):
                if not raw_text:
                    continue
                try:
                    text_conf = float(raw_conf) / 100.0
                except (TypeError, ValueError):
                    continue
                text_conf = _clamp(text_conf, 0.0, 1.0)
                if text_conf < min_text_conf:
                    continue
                for team_number in _normalize_ocr_digits(raw_text):
                    team_key = valid_team_numbers.get(team_number)
                    if not team_key:
                        continue
                    total_hits += 1
                    vote_scores[team_number] += (text_conf * detection_weight)
                    vote_counts[team_number] += 1

        if not vote_scores:
            continue

        ranked = sorted(vote_scores.items(), key=lambda item: item[1], reverse=True)
        best_number, best_score = ranked[0]
        second_score = ranked[1][1] if len(ranked) > 1 else 0.0
        support_count = vote_counts.get(best_number, 0)
        if support_count < 1:
            continue
        if best_score < 0.42:
            continue
        if second_score > 0 and best_score < (second_score * 1.12):
            continue

        track_hints[track_id] = {
            "team_number": int(best_number),
            "team_key": valid_team_numbers[best_number],
            "score": _round(best_score, 4),
            "support_count": int(support_count),
            "second_best_score": _round(second_score, 4),
        }

    return track_hints, {
        "enabled": True,
        "tracks_with_hints": len(track_hints),
        "ocr_reads": int(total_ocr_reads),
        "ocr_hits": int(total_hits),
        "max_samples_per_track": int(max_samples_per_track),
        "min_text_conf": _round(min_text_conf, 4),
        "expand_ratio": _round(expand_ratio, 4),
        "bottom_band_ratio": _round(bottom_ratio, 4),
    }
