import unittest
from contextlib import contextmanager
from unittest.mock import patch
from types import SimpleNamespace

from app.services import scheduler


class SchedulerContextTests(unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self._prior_runtime_state = dict(scheduler._JOB_RUNTIME_STATE_FALLBACK)
        scheduler._JOB_RUNTIME_STATE_FALLBACK.clear()
        self._settings_snapshot = {
            "app_env": scheduler.settings.app_env,
            "strict_startup_env_validation": scheduler.settings.strict_startup_env_validation,
            "storage_cleanup_enabled": scheduler.settings.storage_cleanup_enabled,
            "storage_cleanup_post_analysis": scheduler.settings.storage_cleanup_post_analysis,
            "intel_snapshot_refresh_enabled": scheduler.settings.intel_snapshot_refresh_enabled,
            "freshness_recovery_enabled": scheduler.settings.freshness_recovery_enabled,
            "climb_integrity_audit_enabled": scheduler.settings.climb_integrity_audit_enabled,
            "climb_official_backfill_enabled": scheduler.settings.climb_official_backfill_enabled,
            "automation_regional_enabled": scheduler.settings.automation_regional_enabled,
            "automation_regional_halfday_scheduler_enabled": scheduler.settings.automation_regional_halfday_scheduler_enabled,
            "live_analysis_enabled": scheduler.settings.live_analysis_enabled,
            "live_analysis_regional_auto_enabled": scheduler.settings.live_analysis_regional_auto_enabled,
            "analysis_low_quality_reprocess_enabled": scheduler.settings.analysis_low_quality_reprocess_enabled,
            "ops_smoke_check_enabled": scheduler.settings.ops_smoke_check_enabled,
            "scouting_rooms_cleanup_enabled": scheduler.settings.scouting_rooms_cleanup_enabled,
            "ml_auto_scout_training_export_enabled": scheduler.settings.ml_auto_scout_training_export_enabled,
            "auto_scout_backfill_enabled": scheduler.settings.auto_scout_backfill_enabled,
        }

    def tearDown(self) -> None:
        scheduler._JOB_RUNTIME_STATE_FALLBACK.clear()
        scheduler._JOB_RUNTIME_STATE_FALLBACK.update(self._prior_runtime_state)
        for key, value in self._settings_snapshot.items():
            setattr(scheduler.settings, key, value)
        super().tearDown()

    def test_scheduled_job_lock_contention_yields_none_and_records_skip(self):
        with patch.object(
            scheduler,
            "_acquire_distributed_job_lock",
            return_value=(None, "scheduler:lock:demo", None),
        ), patch.object(scheduler, "_release_distributed_job_lock") as release_lock:
            with scheduler._scheduled_job("demo") as details:
                self.assertIsNone(details)

        state = scheduler._JOB_RUNTIME_STATE_FALLBACK.get("demo") or {}
        self.assertEqual(state.get("last_status"), "ok")
        self.assertEqual(state.get("last_details"), {"status": "skipped_distributed_lock"})
        release_lock.assert_not_called()

    def test_scheduled_job_records_public_details_only(self):
        with patch.object(
            scheduler,
            "_acquire_distributed_job_lock",
            return_value=(None, None, None),
        ):
            with scheduler._scheduled_job("demo") as details:
                self.assertIsInstance(details, dict)
                details["processed"] = 3
                details["_private"] = "ignored"

        state = scheduler._JOB_RUNTIME_STATE_FALLBACK.get("demo") or {}
        self.assertEqual(state.get("last_status"), "ok")
        self.assertEqual(state.get("last_details"), {"processed": 3})

    def test_cleanup_job_returns_early_when_lock_is_held(self):
        with patch.object(
            scheduler,
            "_acquire_distributed_job_lock",
            return_value=(None, "scheduler:lock:cleanup_old_media", None),
        ), patch.object(scheduler, "cleanup_old_media") as cleanup:
            scheduler._scheduled_cleanup_old_media()

        cleanup.assert_not_called()

    def test_auto_scout_export_job_calls_export_service(self):
        @contextmanager
        def _fake_job(*_args, **_kwargs):
            yield {"_db": object()}

        with patch.object(scheduler, "_scheduled_job", _fake_job), patch.object(
            scheduler,
            "export_auto_scout_training_snapshots",
            return_value={
                "approved_drafts": 2,
                "rows_written": 12,
                "skipped_missing_context": 0,
                "skipped_missing_target": 0,
            },
        ) as export:
            scheduler._scheduled_export_auto_scout_training_data()

        export.assert_called_once()

    def test_auto_scout_backfill_job_calls_helper(self):
        @contextmanager
        def _fake_job(*_args, **_kwargs):
            yield {"_db": object()}

        with patch.object(scheduler, "_scheduled_job", _fake_job), patch.object(
            scheduler,
            "backfill_missing_auto_scout_drafts",
            return_value={
                "runs_processed": 3,
                "drafts_created": 7,
                "matches_with_missing": 3,
                "errors": [],
            },
        ) as backfill:
            scheduler._scheduled_auto_scout_draft_backfill()

        backfill.assert_called_once()

    def test_auto_scout_backfill_registration_respects_disabled_setting(self):
        for key in self._settings_snapshot:
            setattr(scheduler.settings, key, False)
        scheduler.settings.app_env = "development"
        scheduler.settings.strict_startup_env_validation = False

        def _make_fake():
            registered: list[str] = []
            fake = SimpleNamespace(
                running=False,
                remove_all_jobs=lambda: None,
                add_job=lambda *_a, **kw: registered.append(kw.get("id")),
                start=lambda: None,
            )
            return fake, registered

        scheduler.settings.auto_scout_backfill_enabled = True
        fake_on, registered_on = _make_fake()
        with patch.object(scheduler, "get_scheduler", return_value=fake_on):
            scheduler.start_scheduler()
        self.assertIn("auto_scout_draft_backfill", registered_on)

        scheduler.settings.auto_scout_backfill_enabled = False
        fake_off, registered_off = _make_fake()
        with patch.object(scheduler, "get_scheduler", return_value=fake_off):
            scheduler.start_scheduler()
        self.assertNotIn("auto_scout_draft_backfill", registered_off)

    def test_runtime_metrics_serialization_does_not_fail_on_non_json_types(self):
        with patch.object(scheduler, "_runtime_metrics_redis", return_value=None):
            scheduler._persist_job_runtime_state(
                "demo",
                {
                    "raw_object": object(),
                    "nested": {"when": scheduler.datetime.now(scheduler.timezone.utc)},
                },
            )
        state = scheduler._JOB_RUNTIME_STATE_FALLBACK.get("demo") or {}
        self.assertIsInstance(state.get("raw_object"), str)
        self.assertIsInstance((state.get("nested") or {}).get("when"), str)

    def test_start_scheduler_raises_on_registration_failure_in_production_like_env(self):
        scheduler.settings.app_env = "production"
        scheduler.settings.strict_startup_env_validation = False
        scheduler.settings.storage_cleanup_enabled = True
        scheduler.settings.storage_cleanup_post_analysis = True
        scheduler.settings.intel_snapshot_refresh_enabled = False
        scheduler.settings.freshness_recovery_enabled = False
        scheduler.settings.climb_integrity_audit_enabled = False
        scheduler.settings.climb_official_backfill_enabled = False
        scheduler.settings.automation_regional_enabled = False
        scheduler.settings.automation_regional_halfday_scheduler_enabled = False
        scheduler.settings.live_analysis_enabled = False
        scheduler.settings.live_analysis_regional_auto_enabled = False
        scheduler.settings.analysis_low_quality_reprocess_enabled = False
        scheduler.settings.ops_smoke_check_enabled = False
        scheduler.settings.scouting_rooms_cleanup_enabled = False
        scheduler.settings.ml_auto_scout_training_export_enabled = False

        fake_scheduler = SimpleNamespace(
            running=False,
            remove_all_jobs=lambda: None,
            add_job=lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("boom")),
            start=lambda: None,
        )
        with patch.object(scheduler, "get_scheduler", return_value=fake_scheduler):
            with self.assertRaises(RuntimeError):
                scheduler.start_scheduler()

    def test_start_scheduler_warns_but_does_not_raise_in_development(self):
        scheduler.settings.app_env = "development"
        scheduler.settings.strict_startup_env_validation = False
        scheduler.settings.storage_cleanup_enabled = True
        scheduler.settings.storage_cleanup_post_analysis = True
        scheduler.settings.intel_snapshot_refresh_enabled = False
        scheduler.settings.freshness_recovery_enabled = False
        scheduler.settings.climb_integrity_audit_enabled = False
        scheduler.settings.climb_official_backfill_enabled = False
        scheduler.settings.automation_regional_enabled = False
        scheduler.settings.automation_regional_halfday_scheduler_enabled = False
        scheduler.settings.live_analysis_enabled = False
        scheduler.settings.live_analysis_regional_auto_enabled = False
        scheduler.settings.analysis_low_quality_reprocess_enabled = False
        scheduler.settings.ops_smoke_check_enabled = False
        scheduler.settings.scouting_rooms_cleanup_enabled = False
        scheduler.settings.ml_auto_scout_training_export_enabled = False

        fake_scheduler = SimpleNamespace(
            running=False,
            remove_all_jobs=lambda: None,
            add_job=lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("boom")),
            start=lambda: None,
        )
        with patch.object(scheduler, "get_scheduler", return_value=fake_scheduler):
            scheduler.start_scheduler()


if __name__ == "__main__":
    unittest.main()
