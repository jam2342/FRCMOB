from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest

from scripts.eval_detector import (
    _capture_thresholds,
    _cohort,
    _ground_truth_density,
    _isolated_eval,
    _precision_at_recall,
    _threshold_report,
    evaluate,
)


def make_dataset(root: Path) -> None:
    for part in ("images", "labels"):
        (root / part / "val").mkdir(parents=True)
    (root / "images/val/robot.JPEG").write_bytes(b"image bytes")
    (root / "labels/val/robot.txt").write_text("0 0.5 0.5 0.2 0.3\n")
    (root / "images/val/empty.png").write_bytes(b"negative image")
    (root / "labels/val/empty.txt").write_text("")


def test_density_uses_same_images_including_explicit_negatives_and_jpeg(tmp_path):
    make_dataset(tmp_path)
    entries = _cohort(tmp_path, "val")
    assert _ground_truth_density(entries) == {
        "frames": 2, "boxes": 1, "boxes_per_frame": 0.5,
        "median_boxes_per_frame": 0.5, "negative_frames": 1,
    }
    before = entries
    (tmp_path / "labels/val/robot.txt").write_text("0 0.4 0.5 0.2 0.3\n")
    assert _cohort(tmp_path, "val") != before


@pytest.mark.parametrize("mode", ["missing", "orphan", "nan", "class", "degenerate"])
def test_invalid_or_incomplete_review_fails(tmp_path, mode):
    make_dataset(tmp_path)
    label = tmp_path / "labels/val/robot.txt"
    if mode == "missing":
        label.unlink()
    elif mode == "orphan":
        (tmp_path / "labels/val/orphan.txt").write_text("")
    else:
        label.write_text({
            "nan": "0 nan 0.5 0.2 0.3",
            "class": "1 0.5 0.5 0.2 0.3",
            "degenerate": "0 0.5 0.5 0 0.3",
        }[mode])
    with pytest.raises(ValueError):
        _cohort(tmp_path, "val")


def test_temporary_validation_cannot_repair_or_cache_into_locked_source(tmp_path):
    make_dataset(tmp_path)
    original = _cohort(tmp_path, "val")
    with _isolated_eval(tmp_path, "val", original) as (yaml, temporary):
        assert yaml.is_file()
        assert not (temporary / "images/val/robot.JPEG").is_symlink()
        (temporary / "images/val/robot.JPEG").write_bytes(b"library repaired JPEG")
        (temporary / "labels/val/robot.txt").write_text("")
        (temporary / "labels/val.cache").write_bytes(b"cache")
        assert _cohort(tmp_path, "val") == original
    assert not temporary.exists()
    assert not (tmp_path / "labels/val.cache").exists()


def test_unattainable_recall_has_no_interpolated_precision():
    box = SimpleNamespace(
        px=np.array([0.0, 0.5, 1.0]), prec_values=np.array([[1.0, 0.8, 0.0]]),
        r_curve=np.array([[0.79, 0.5, 0.0]]),
    )
    assert _precision_at_recall(box, (0.5, 0.8, 0.9)) == {
        "precision_at_recall_0.50": 0.8,
        "precision_at_recall_0.80": None,
        "precision_at_recall_0.90": None,
    }


class FakeValidator:
    def _prepare_batch(self, index, batch):
        return {"cls": np.zeros(batch["targets"][index])}

    def _prepare_pred(self, prediction):
        return prediction

    def _process_batch(self, prediction, truth):
        matches = np.zeros((len(prediction["conf"]), 10), dtype=bool)
        if len(matches) and len(truth["cls"]):
            # Lower-confidence box wins when both are present; rematching after
            # thresholding must still count the retained higher-confidence box.
            matches[-1, :] = True
        return {"tp": matches}

    def update_metrics(self, predictions, batch):
        pass


def test_threshold_metrics_rematch_and_include_false_positives_on_negative_frames():
    totals = {
        value: dict(frames=0, ground_truth_boxes=0, detections=0, true_positives=0)
        for value in (0.2, 0.01)
    }
    _capture_thresholds(
        FakeValidator(),
        [{"conf": np.array([0.9, 0.05])}, {"conf": np.array([0.8])}],
        {"targets": [1, 0]}, totals,
    )
    report = _threshold_report(0.2, totals[0.2])
    assert report["true_positives"] == 1
    assert report["false_positives"] == 1
    assert report["false_negatives"] == 0
    assert report["precision"] == 0.5
    assert report["recall"] == 1.0
    assert report["frames"] == 2
    assert totals[0.01]["detections"] == 3


def test_all_confidence_metrics_share_one_validation_pass_and_explicit_protocol(tmp_path):
    # Patches ultralytics itself; only installed with requirements-training.txt.
    pytest.importorskip("ultralytics")
    make_dataset(tmp_path)
    weights = tmp_path / "model.pt"
    weights.write_bytes(b"weights")
    calls = []

    class FakeModel:
        names = {0: "robot"}

        def __init__(self, path):
            pass

        def val(self, **kwargs):
            calls.append(kwargs)
            validator = kwargs["validator"]()
            validator.update_metrics(
                [{"conf": np.array([0.8])}, {"conf": np.array([0.9, 0.05])}],
                {"targets": [0, 1]},
            )
            box = SimpleNamespace(
                map50=0.7, map=0.5, mp=0.9, mr=0.6,
                px=np.array([0.0, 0.5, 1.0]),
                prec_values=np.array([[1.0, 0.8, 0.0]]),
                r_curve=np.array([[0.8, 0.5, 0.0]]),
            )
            return SimpleNamespace(box=box)

    with (
        patch("ultralytics.YOLO", FakeModel),
        patch("ultralytics.models.yolo.detect.DetectionValidator", FakeValidator),
    ):
        result = evaluate(weights, tmp_path, "val", (640,), 0.17, False)
    assert len(calls) == 1
    assert calls[0]["batch"] == 1
    assert calls[0]["device"] == "cpu"
    assert calls[0]["rect"] is False
    assert calls[0]["iou"] == 0.6
    assert calls[0]["max_det"] == 50
    assert calls[0]["conf"] == 0.001
    assert not Path(calls[0]["data"]).exists()
    row = result["by_resolution"][0]
    assert row["at_serving_conf"]["conf"] == 0.17
    assert row["at_serving_conf"]["precision"] == 0.5
    assert len(row["count_bias"]) == 6
    assert len(result["model_sha256"]) == 64
    assert len(result["dataset_sha256"]) == 64
    assert result["ground_truth"]["frames"] == 2
    assert result["protocol"]["rect"] is False
