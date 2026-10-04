from __future__ import annotations

from unittest.mock import patch

from app.api import routes_maintenance
from tests.conftest import DBTestCase


class OpsMetricsTests(DBTestCase):
    def test_metrics_carry_no_broadcast_video_blocks(self):
        with (
            patch.object(routes_maintenance, "get_scheduler_runtime_metrics", return_value={}),
            patch.object(routes_maintenance.statbotics_client, "runtime_metrics", return_value={}),
            patch.object(routes_maintenance, "get_fuel_phase_calibration", return_value={}),
            patch.object(routes_maintenance, "_regional_automation_snapshot", return_value={}),
        ):
            metrics = routes_maintenance._ops_metrics_payload(self.db, 7)

        for retired in ("analysis_runs", "tracking_backend", "freshness", "freshness_recovery"):
            self.assertNotIn(retired, metrics)
        self.assertEqual(metrics["on_device_sessions"]["total"], 0)

    def test_open_statbotics_circuit_raises_an_alert(self):
        metrics = {"statbotics_runtime": {"circuits": {"open_count": 2}}}
        codes = {alert["code"] for alert in routes_maintenance._ops_alerts_from_metrics(metrics)}
        self.assertEqual(codes, {"statbotics_circuit_open"})
