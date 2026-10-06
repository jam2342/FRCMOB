from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from app.services.events import pipeline


class RefreshEventRebuildsOnceTests(unittest.TestCase):
    def test_ingest_skips_its_own_rebuild_and_refresh_runs_it_once(self):
        ingest = MagicMock(return_value={"ok": True})
        rebuild = MagicMock(return_value={"synergy": {}, "ratings": {"ok": True}, "ml_shadow": {}})
        with (
            patch("app.services.events.ingest.ingest_event", ingest),
            patch("app.tba.client.TBAClient", MagicMock()),
            patch.object(pipeline, "post_compute_event", rebuild),
        ):
            pipeline.refresh_event(
                MagicMock(),
                event_key="2026test",
                run_post_compute=True,
                synergy_model_version="v",
                quality_threshold=0.7,
            )
        self.assertIs(ingest.call_args.kwargs.get("run_post_compute"), False)
        rebuild.assert_called_once()


if __name__ == "__main__":
    unittest.main()
