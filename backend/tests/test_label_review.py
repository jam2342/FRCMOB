from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
from PIL import Image

from scripts.audit_labels import main as audit_main
from scripts.audit_labels import render_boxes
from scripts.dataset_safety import validate_audit
from scripts.review_labels import apply_review, export_review


@pytest.fixture
def dataset(tmp_path: Path) -> Path:
    root = tmp_path / "source"
    for split in ("train", "val"):
        (root / "images" / split).mkdir(parents=True)
        (root / "labels" / split).mkdir(parents=True)
        Image.new("RGB", (100, 80), "white").save(root / "images" / split / "frame.jpg")
        (root / "labels" / split / "frame.txt").write_text("0 0.5 0.5 0.2 0.3\n")
    return root


def review_for(root: Path) -> dict:
    review = export_review(root, ["train", "val"], 10)
    review["reviewer"] = "test reviewer"
    return review


def test_correction_copy_preserves_source_and_never_inherits_acceptance(dataset, tmp_path):
    review = review_for(dataset)
    review["frames"][0].update(action="replace", reason="box missed a bumper",
                               boxes=[[0, 0.5, 0.5, 0.4, 0.4]])
    review["frames"][1].update(action="skip", reason="score graphic")
    (dataset / "audit").mkdir()
    (dataset / "audit" / "AUDIT.json").write_text('{"status":"accepted"}')
    output = tmp_path / "corrected"
    result = apply_review(dataset, output, review)
    assert result["actions"] == {"kept": 0, "replaced": 1, "skipped": 1, "pending": 0}
    assert (dataset / "labels/train/frame.txt").read_text() == "0 0.5 0.5 0.2 0.3\n"
    assert (output / "labels/train/frame.txt").read_text() == "0 0.50000000 0.50000000 0.40000000 0.40000000\n"
    assert not (output / "images/val/frame.jpg").exists()
    assert json.loads((output / "audit/AUDIT.json").read_text())["status"] == "needs_review"
    with pytest.raises(ValueError):
        validate_audit(output, ["train", "val"])


@pytest.mark.parametrize("mutation", ["label", "image"])
def test_stale_decisions_rejected_before_output(dataset, tmp_path, mutation):
    review = review_for(dataset)
    if mutation == "label":
        (dataset / "labels/train/frame.txt").write_text("")
    else:
        Image.new("RGB", (100, 80), "black").save(dataset / "images/train/frame.jpg")
    with pytest.raises(ValueError, match="Stale"):
        apply_review(dataset, tmp_path / "output", review)
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize("bad_boxes", [
    [[0, 0.5, 0.5, -0.2, 0.3]], [[0, float("nan"), 0.5, 0.2, 0.3]],
    [[0, 0, 0.5, 0.5, 0.3]], [[1, 0.5, 0.5, 0.2, 0.3]], [[0, 0.5]],
])
def test_invalid_corrections_rejected_before_copy(dataset, tmp_path, bad_boxes):
    review = review_for(dataset)
    review["frames"][0].update(action="replace", reason="test", boxes=bad_boxes)
    with pytest.raises(ValueError):
        apply_review(dataset, tmp_path / "output", review)
    assert not (tmp_path / "output").exists()


def test_source_overlap_unknown_frame_and_duplicate_are_rejected(dataset, tmp_path):
    review = review_for(dataset)
    with pytest.raises(ValueError, match="overlaps"):
        apply_review(dataset, dataset / "copy", review)
    review["frames"].append(dict(review["frames"][0]))
    with pytest.raises(ValueError, match="duplicate"):
        apply_review(dataset, tmp_path / "out", review)
    review["frames"][-1]["image"] = "../outside.jpg"
    with pytest.raises(ValueError, match="Unknown"):
        apply_review(dataset, tmp_path / "out", review)


def test_pending_frames_preserved_and_partial_split_export_cannot_drop_val(dataset, tmp_path):
    review = review_for(dataset)
    result = apply_review(dataset, tmp_path / "copy", review)
    assert result["actions"]["pending"] == 2
    review["splits"] = ["train"]
    with pytest.raises(ValueError, match="every source split"):
        apply_review(dataset, tmp_path / "copy2", review)


def test_holdout_and_alias_cannot_be_reviewed(tmp_path):
    locked = tmp_path / "frc_einstein_2026_holdout_reviewed_yolo"
    locked.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(locked, target_is_directory=True)
    for root in (locked, alias):
        with pytest.raises(ValueError, match="holdout"):
            export_review(root, ["val"], 1)


def test_audit_crop_index_and_edge_overlay_are_correct(dataset, tmp_path):
    image = dataset / "images/train/frame.jpg"
    labels = dataset / "labels/train"
    # Box touches the left edge: requested padding is clipped, actual padding is zero.
    (labels / "frame.txt").write_text("0 0.1 0.5 0.2 0.4\n")
    output = tmp_path / "boxes"
    assert render_boxes([image], labels, output, 40) == 1
    rows = list(csv.DictReader((output / "index.csv").open()))
    assert rows[0]["image"] == "frame.jpg"
    assert rows[0]["label_index"] == "0"
    with Image.open(output / "boxes_000.jpg") as rendered:
        # Height determines scaling: the rectangle begins around x=40, not x=62
        # where the old, unclipped-padding calculation incorrectly drew it.
        r, g, b = rendered.getpixel((40, 45))
        assert g > r + 40 and g > b + 40


def test_rerunning_audit_does_not_erase_human_verdict(dataset, monkeypatch):
    audit = dataset / "audit"
    audit.mkdir()
    verdict = audit / "AUDIT.md"
    verdict.write_text("Reviewer correction: not accepted")
    monkeypatch.setattr("sys.argv", ["audit_labels.py", "--dataset", str(dataset)])
    assert audit_main() == 2
    assert verdict.read_text() == "Reviewer correction: not accepted"
