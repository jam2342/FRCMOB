from __future__ import annotations

import unittest

from app.db import models
from app.services.auto_scout import scouting as auto_scouting
from app.services.auto_scout.ml import AUTO_SCOUT_ROUND2_ML_FIELDS, auto_scout_field_scope
from app.services.auto_scout.training import export_auto_scout_training_snapshots
from tests.conftest import DBTestCase
from tests.test_auto_scouting import _seed_analysis_rows, _seed_core_entities


class AutoScoutTrainingExportTests(DBTestCase):
    def _seed_approved_draft(self) -> tuple[str, str, str]:
        event_key, match_key, team_key = _seed_core_entities(self.db)
        _seed_analysis_rows(self.db, event_key=event_key, match_key=match_key, team_key=team_key)
        draft, _ = auto_scouting.generate_auto_scout_draft(
            self.db,
            event_key=event_key,
            match_key=match_key,
            team_key=team_key,
        )
        auto_scouting.approve_auto_scout_draft(
            self.db,
            draft_id=draft.id,
            draft_version=draft.draft_version,
            approved_by="Test",
            edited_payload={
                "form_patch": (draft.draft_payload or {}).get("form_patch", {}),
                "notes_seed": "",
                "derived_insights": (draft.draft_payload or {}).get("derived_insights", {}),
            },
        )
        return event_key, match_key, team_key

    def test_export_writes_round2_snapshots_from_approved_draft(self) -> None:
        event_key, match_key, team_key = self._seed_approved_draft()
        result = export_auto_scout_training_snapshots(
            self.db,
            source_version="auto_scout_field_features_v1",
            season_year=2026,
            replace_existing=True,
            max_drafts=200,
        )
        self.assertTrue(result.get("ok"))
        self.assertGreaterEqual(int(result.get("rows_written") or 0), len(AUTO_SCOUT_ROUND2_ML_FIELDS))

        scope = auto_scout_field_scope("offense_level_1_5")
        row = (
            self.db.query(models.MLFeatureSnapshot)
            .filter(
                models.MLFeatureSnapshot.scope == scope,
                models.MLFeatureSnapshot.event_key == event_key,
                models.MLFeatureSnapshot.match_key == match_key,
                models.MLFeatureSnapshot.team_key == team_key,
            )
            .one_or_none()
        )
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row.split_tag, "holdout")
        self.assertIsNotNone((row.target or {}).get("field_value"))

    def test_export_rejects_unknown_field_name(self) -> None:
        self._seed_approved_draft()
        with self.assertRaises(RuntimeError):
            export_auto_scout_training_snapshots(
                self.db,
                source_version="auto_scout_field_features_v1",
                season_year=2026,
                field_names=["unknown_field"],
            )


if __name__ == "__main__":
    unittest.main()
