import unittest

try:
    from fastapi import HTTPException
    from app.api.routes_analysis import (
        _ensure_analysis_write_enabled,
        enqueue_analysis,
        enqueue_event_analysis,
        run_event_pipeline,
    )
    from app.core.config import settings
    _IMPORT_ERROR: Exception | None = None
except Exception as exc:  # pragma: no cover - dependency-gated test import
    _IMPORT_ERROR = exc


@unittest.skipIf(_IMPORT_ERROR is not None, f"optional api deps unavailable: {_IMPORT_ERROR}")
class PublicModeGuardTests(unittest.TestCase):
    def setUp(self):
        self._prior_public_readonly_mode = bool(settings.public_readonly_mode)

    def tearDown(self):
        settings.public_readonly_mode = self._prior_public_readonly_mode

    def test_guard_blocks_when_public_mode_enabled(self):
        settings.public_readonly_mode = True
        with self.assertRaises(HTTPException) as context:
            _ensure_analysis_write_enabled()
        self.assertEqual(context.exception.status_code, 403)
        self.assertIn("disabled in public mode", str(context.exception.detail).lower())

    def test_guard_allows_when_public_mode_disabled(self):
        settings.public_readonly_mode = False
        _ensure_analysis_write_enabled()

    def test_event_pipeline_route_blocks_before_db_access(self):
        settings.public_readonly_mode = True
        with self.assertRaises(HTTPException) as context:
            run_event_pipeline("2026test", db=None)  # type: ignore[arg-type]
        self.assertEqual(context.exception.status_code, 403)

    def test_enqueue_routes_block_before_db_access(self):
        settings.public_readonly_mode = True
        with self.assertRaises(HTTPException) as context_match:
            enqueue_analysis("2026test_qm1", db=None)  # type: ignore[arg-type]
        self.assertEqual(context_match.exception.status_code, 403)

        with self.assertRaises(HTTPException) as context_event:
            enqueue_event_analysis("2026test", db=None)  # type: ignore[arg-type]
        self.assertEqual(context_event.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
