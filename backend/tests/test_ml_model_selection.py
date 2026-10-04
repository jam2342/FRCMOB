"""Unreleased candidates must not become live models through implicit fallback."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from app.core.config import settings
from app.api import routes_events
from app.db import models
from app.services.ml import shadow
from tests.conftest import DBTestCase
from tests.test_routes_events_schedule_ml import _seed_minimal_schedule, _seed_ratings


class ModelSelectionTests(DBTestCase):
    def add_model(self, version, *, active=False, model_key="match_outcome", age_days=0):
        row = models.MLModelRegistry(
            model_key=model_key,
            model_version=version,
            artifact_path="media/models/shadow/disposable.pt",
            is_active=active,
            trained_at=datetime.now(timezone.utc) - timedelta(days=age_days),
        )
        self.db.add(row)
        self.db.commit()
        return row

    def test_inactive_candidate_without_release_returns_no_live_model(self):
        self.add_model("unreleased")
        with mock.patch.object(settings, "ml_shadow_match_outcome_model_version", ""):
            self.assertIsNone(shadow._load_model_registry_row(self.db, model_key="match_outcome"))
            with mock.patch.object(shadow, "_load_model_runtime_cached") as runtime:
                result = shadow.infer_match_outcome_shadow_from_rows(self.db, rows=[{"match_key": "2026test_qm1"}])
            self.assertEqual(result["reason"], "model_not_found")
            runtime.assert_not_called()

    def test_newer_candidate_does_not_replace_active_release(self):
        self.add_model("released", active=True, age_days=1)
        self.add_model("unreleased")
        with mock.patch.object(settings, "ml_shadow_match_outcome_model_version", ""):
            row = shadow._load_model_registry_row(self.db, model_key="match_outcome")
        self.assertEqual(row.model_version, "released")

    def test_environment_pin_cannot_activate_an_inactive_candidate(self):
        self.add_model("unreleased")
        with mock.patch.object(settings, "ml_shadow_match_outcome_model_version", "unreleased"):
            self.assertIsNone(shadow._load_model_registry_row(self.db, model_key="match_outcome"))

    def test_explicit_candidate_version_can_be_evaluated(self):
        self.add_model("unreleased")
        row = shadow._load_model_registry_row(self.db, model_key="match_outcome", model_version="unreleased")
        self.assertEqual(row.model_version, "unreleased")
        self.assertFalse(row.is_active)

    def test_live_schedule_ignores_candidate_even_with_production_blend_enabled(self):
        _seed_minimal_schedule(self.db)
        _seed_ratings(self.db)
        self.add_model("unreleased")
        with (
            mock.patch.object(settings, "ml_shadow_enabled", True),
            mock.patch.object(settings, "ml_match_outcome_blend", 0.2),
            mock.patch.object(settings, "ml_shadow_match_outcome_model_version", ""),
            mock.patch.object(shadow, "_load_model_runtime_cached") as runtime,
        ):
            payload = routes_events.get_event_schedule_with_synergy(
                event_key="2026txhou", request=SimpleNamespace(),
                synergy_model_version="test", include_pair_breakdown=False,
                include_ml_predictions=True, refresh=False, db=self.db,
            )
        runtime.assert_not_called()
        prediction = payload["matches"][0]["prediction"]
        self.assertEqual(prediction["source_label"], "deterministic_rating_synergy_v1")
        self.assertEqual(prediction["red_win_prob"], prediction["red_win_prob_deterministic"])
        self.assertEqual(prediction["prediction_blend"], 0.0)
        self.assertIsNone(prediction["red_win_prob_ml"])

    def test_candidate_materialization_names_the_just_trained_versions(self):
        snapshot_result = {"team_strength": {"rows_written": 10}, "match_outcome": {"rows_written": 10}}
        with (
            mock.patch.object(shadow, "rebuild_feature_snapshots", return_value=snapshot_result),
            mock.patch.object(shadow, "train_team_strength_shadow_model", return_value={
                "ok": True, "model_version": "ts_candidate", "metrics": {"val_mae": 0.2, "val_r2": 0.9}
            }),
            mock.patch.object(shadow, "train_match_outcome_shadow_model", return_value={
                "ok": True, "model_version": "mo_candidate", "metrics": {"val_brier": 0.1, "val_accuracy": 0.8}
            }),
            mock.patch.object(shadow, "_activate_model_version") as activate,
            mock.patch.object(shadow, "materialize_shadow_predictions_for_event", return_value={
                "predictions": {"match_outcome": {"ok": True}}
            }) as materialize,
        ):
            result = shadow.auto_train_shadow_models_for_event_breakdown(self.db, event_key="2026test")
        self.assertTrue(result["ok"])
        self.assertFalse(result["activate"])
        activate.assert_not_called()
        self.assertEqual(materialize.call_args.kwargs["team_strength_model_version"], "ts_candidate")
        self.assertEqual(materialize.call_args.kwargs["match_outcome_model_version"], "mo_candidate")


@pytest.mark.parametrize("train, model_key, extra", [
    (shadow.train_team_strength_shadow_model, "team_strength", {}),
    (shadow.train_match_outcome_shadow_model, "match_outcome", {}),
    (shadow.train_auto_scout_field_shadow_model, "auto_scout_field:offense_level_1_5", {"field_name": "offense_level_1_5"}),
    (shadow.train_synergy_pair_shadow_model, "synergy_pair", {}),
    (shadow.train_role_signal_shadow_model, "role_signal:defender", {"signal_name": "defender"}),
])
def test_training_creates_a_candidate_without_replacing_the_release(train, model_key, extra, db_session, tmp_path, monkeypatch):
    import torch

    monkeypatch.setattr(settings, "ml_shadow_model_dir", str(tmp_path))
    release = models.MLModelRegistry(
        model_key=model_key, model_version="released", artifact_path="unused.pt", is_active=True
    )
    db_session.add(release)
    db_session.commit()

    def synthetic_dataset(_snapshots, *, feature_order, **_kwargs):
        # Fitting, artifact writing/loading and registry updates remain real.
        width = len(feature_order)
        return shadow._PreparedDataset(
            feature_order=feature_order, train_x=torch.randn(20, width),
            train_y=torch.tensor([0.0, 1.0] * 10), val_x=torch.randn(5, width),
            val_y=torch.tensor([0.0, 1.0, 0.0, 1.0, 0.0]),
            mean=torch.zeros(width), std=torch.ones(width), train_count=20, val_count=5,
        )

    monkeypatch.setattr(shadow, "_prepare_dataset", synthetic_dataset)
    result = train(db_session, model_version="candidate", epochs=1, **extra)
    assert result["ok"] and result["is_active"] is False
    db_session.refresh(release)
    assert release.is_active is True
    candidate = shadow._load_model_registry_row(db_session, model_key=model_key, model_version="candidate")
    assert candidate is not None and candidate.is_active is False
    artifact = Path(result["artifact_path"])
    assert artifact.is_file() and artifact.stat().st_size > 0
    runtime = shadow._load_model_runtime(candidate)
    assert len(runtime["feature_order"]) > 0


@pytest.mark.parametrize("flags, expected", [([], False), (["--activate"], True), (["--no-activate"], False)])
def test_training_cli_requires_explicit_activation(flags, expected, monkeypatch, capsys):
    from scripts import train_ml_shadow_models as cli

    monkeypatch.setattr("sys.argv", ["train_ml_shadow_models", "--model-key", "match_outcome", "--skip-data-gates", *flags])
    with mock.patch.object(cli, "SessionLocal"), mock.patch.object(cli, "_train_single_model", return_value={"ok": True}) as train:
        assert cli.main() == 0
    assert train.call_args.kwargs["activate"] is expected
    capsys.readouterr()
