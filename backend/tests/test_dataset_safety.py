from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from scripts import train_frc_yolo as trainer
from scripts.build_v3_dataset import camera_group
from scripts.dataset_safety import (
    assert_fresh_output,
    assert_not_holdout,
    dataset_fingerprint,
    dataset_images,
    validate_audit,
)
from scripts.propose_missing_labels import unsafe_temporal_groups


@pytest.fixture
def corpus(tmp_path):
    root = tmp_path / "corpus"
    for split, prefix, color in (("train", "match10", "red"), ("val", "match20", "blue")):
        (root / "images" / split).mkdir(parents=True)
        (root / "labels" / split).mkdir(parents=True)
        Image.new("RGB", (64, 64), color).save(root / "images" / split / f"{prefix}_001.jpg")
        (root / "labels" / split / f"{prefix}_001.txt").write_text("0 0.5 0.5 0.2 0.2\n")
    (root / "dataset.yaml").write_text(
        f"path: {root}\ntrain: images/train\nval: images/val\nnames:\n  0: robot\n")
    return root


def accepted_audit(root):
    fingerprint, count, _ = dataset_fingerprint(root, ["train", "val"])
    audit = dict(version=1, status="accepted", dataset_sha256=fingerprint,
                 splits=["train", "val"], reviewer="Fixture human", reviewer_kind="human",
                 reviewed_frames=count, label_recall=1, false_positive_rate=0,
                 reference_density=1)
    (root / "audit").mkdir(exist_ok=True)
    (root / "audit/AUDIT.json").write_text(json.dumps(audit))
    return audit


def test_holdout_descendants_and_symlinks_refused(tmp_path):
    locked = tmp_path / "frc_einstein_2026_holdout_reviewed_yolo"
    locked.mkdir()
    alias = tmp_path / "innocent-name"
    alias.symlink_to(locked, target_is_directory=True)
    for path in (locked, locked / "images", alias, alias / "new-output"):
        with pytest.raises(ValueError, match="holdout"):
            assert_not_holdout(path)


def test_output_overlap_and_stale_files_refused(corpus, tmp_path):
    for output in (corpus, corpus / "output", corpus.parent):
        with pytest.raises(ValueError):
            assert_fresh_output(output, [corpus])
    fresh = tmp_path / "fresh"
    assert_fresh_output(fresh, [corpus])
    fresh.mkdir()
    (fresh / "old-label.txt").write_text("old")
    with pytest.raises(ValueError, match="stale"):
        assert_fresh_output(fresh, [corpus])


def test_symlinked_labels_outside_dataset_refused(corpus, tmp_path):
    label = corpus / "labels/train/match10_001.txt"
    label.unlink()
    external = tmp_path / "external.txt"
    external.write_text("")
    label.symlink_to(external)
    with pytest.raises(ValueError, match="escapes"):
        dataset_images(corpus, "train")


@pytest.mark.parametrize("field,value", [
    ("status", "needs_review"), ("reviewer_kind", "assisted"), ("reviewed_frames", 0),
    ("label_recall", float("nan")), ("false_positive_rate", .1),
    ("reference_density", 2), ("splits", ["train"]),
])
def test_audit_gate_rejects_bad_evidence(corpus, field, value):
    audit = accepted_audit(corpus)
    audit[field] = value
    (corpus / "audit/AUDIT.json").write_text(json.dumps(audit))
    with pytest.raises(ValueError):
        validate_audit(corpus, ["train", "val"])


def test_content_change_invalidates_accepted_audit(corpus):
    accepted_audit(corpus)
    validate_audit(corpus, ["train", "val"])
    (corpus / "labels/train/match10_001.txt").write_text("")
    with pytest.raises(ValueError, match="stale"):
        validate_audit(corpus, ["train", "val"])


def test_extracted_frames_group_by_clip():
    assert camera_group("match-video_t0001500")[0] == camera_group("match-video_t0032500")[0]


def test_temporal_veto_needs_orientation_provenance(corpus):
    images = dataset_images(corpus, "train")
    assert unsafe_temporal_groups(corpus, "train", images) == {"match10"}
    manifest = {"frames": [{"image": images[0].name, "split": "train", "temporal_filters_safe": True}]}
    (corpus / "DATASET_PROVENANCE.json").write_text(json.dumps(manifest))
    assert unsafe_temporal_groups(corpus, "train", images) == set()
    manifest["frames"][0]["temporal_filters_safe"] = False
    (corpus / "DATASET_PROVENANCE.json").write_text(json.dumps(manifest))
    assert unsafe_temporal_groups(corpus, "train", images) == {"match10"}


def test_train_rejects_source_and_content_leakage(corpus):
    train = corpus / "images/train/match10_001.jpg"
    val = corpus / "images/val/match20_001.jpg"
    val.write_bytes(train.read_bytes())
    with pytest.raises(ValueError, match="Identical"):
        trainer._training_dataset(corpus / "dataset.yaml", experiment_only=True)
    val.rename(val.with_name("match10_002.jpg"))
    label = corpus / "labels/val/match20_001.txt"
    label.rename(label.with_name("match10_002.txt"))
    with pytest.raises(ValueError, match="Source video"):
        trainer._training_dataset(corpus / "dataset.yaml", experiment_only=True)


@pytest.fixture
def fake_training(corpus, tmp_path, monkeypatch):
    calls = []
    metrics = SimpleNamespace(map50=.9, mr=.8)
    project = tmp_path / "runs"
    output = tmp_path / "candidate.pt"

    class FakeYOLO:
        def __init__(self, model):
            calls.append(("load", model))

        def train(self, **kwargs):
            calls.append(("train", kwargs))
            run = project / kwargs["name"]
            (run / "weights").mkdir(parents=True)
            (run / "weights/best.pt").write_bytes(b"fixture weights")
            return SimpleNamespace(save_dir=run)

        def val(self, **kwargs):
            calls.append(("val", kwargs))
            assert Path(kwargs["data"]).is_file()
            return SimpleNamespace(box=metrics)

    monkeypatch.setitem(sys.modules, "ultralytics", SimpleNamespace(YOLO=FakeYOLO))
    holdout = tmp_path / "holdout"
    (holdout / "images/val").mkdir(parents=True)
    (holdout / "labels/val").mkdir(parents=True)
    Image.new("RGB", (32, 32)).save(holdout / "images/val/held.jpg")
    (holdout / "labels/val/held.txt").write_text("0 0.5 0.5 0.2 0.2\n")
    monkeypatch.setattr(trainer, "LOCKED_HOLDOUT", holdout)
    argv = ["train_frc_yolo.py", "--data", str(corpus / "dataset.yaml"),
            "--project", str(project), "--output-model", str(output), "--name", "fixture"]
    return SimpleNamespace(calls=calls, metrics=metrics, output=output, argv=argv,
                           holdout=holdout, project=project)


def test_unaudited_production_training_stops_before_model_load(fake_training, monkeypatch):
    monkeypatch.setattr(sys, "argv", fake_training.argv)
    assert trainer.main() == 2
    assert fake_training.calls == []
    assert not fake_training.project.exists()


def test_missing_holdout_cannot_fall_back(corpus, fake_training, monkeypatch):
    accepted_audit(corpus)
    monkeypatch.setattr(trainer, "LOCKED_HOLDOUT", fake_training.holdout / "missing")
    monkeypatch.setattr(sys, "argv", fake_training.argv)
    assert trainer.main() == 2
    assert fake_training.calls == []


def test_renaming_copied_holdout_does_not_allow_training(corpus, fake_training, monkeypatch):
    (corpus / "images/train/match10_001.jpg").write_bytes(
        (fake_training.holdout / "images/val/held.jpg").read_bytes())
    monkeypatch.setattr(sys, "argv", fake_training.argv + ["--experiment-only"])
    assert trainer.main() == 2
    assert fake_training.calls == []


def test_experiment_keeps_checkpoints_without_exporting(fake_training, monkeypatch):
    monkeypatch.setattr(sys, "argv", fake_training.argv + ["--experiment-only", "--lr0", "0.001"])
    assert trainer.main() == 0
    assert not fake_training.output.exists()
    assert (fake_training.project / "fixture/weights/best.pt").is_file()
    kwargs = next(v for k, v in fake_training.calls if k == "train")
    assert kwargs["optimizer"] == "SGD" and kwargs["lr0"] == .001


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -.1, 1.1])
def test_bad_validation_metrics_do_not_export(corpus, fake_training, monkeypatch, bad):
    accepted_audit(corpus)
    fake_training.metrics.map50 = bad
    monkeypatch.setattr(sys, "argv", fake_training.argv)
    assert trainer.main() == 1
    assert not fake_training.output.exists()


def test_human_audit_and_metrics_allow_candidate(corpus, fake_training, monkeypatch):
    accepted_audit(corpus)
    monkeypatch.setattr(sys, "argv", fake_training.argv)
    assert trainer.main() == 0
    assert fake_training.output.read_bytes() == b"fixture weights"
    kwargs = next(v for k, v in fake_training.calls if k == "val")
    assert (kwargs["batch"], kwargs["rect"], kwargs["iou"], kwargs["max_det"]) == (1, False, .6, 50)


def test_holdout_snapshot_cannot_repair_source_image(fake_training):
    import yaml

    original = fake_training.holdout / "images/val/held.jpg"
    before = original.read_bytes()
    with trainer._holdout_yaml() as path:
        temporary = Path(yaml.safe_load(Path(path).read_text())["path"])
        copied = temporary / "images/val/held.jpg"
        assert not copied.is_symlink()
        copied.write_bytes(b"a simulated JPEG repair")
    assert original.read_bytes() == before
