import tempfile
import unittest
from pathlib import Path

try:
    from app.services.media.storage_cleanup import _verify_paths_removed

    _IMPORT_ERROR: Exception | None = None
except Exception as exc:  # pragma: no cover - optional dependency guarded import
    _IMPORT_ERROR = exc


@unittest.skipIf(_IMPORT_ERROR is not None, f"optional deps unavailable: {_IMPORT_ERROR}")
class StorageCleanupVerificationTests(unittest.TestCase):
    def test_verify_paths_removed_reports_existing_paths(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            test_path = Path(temp_dir) / "keep.txt"
            test_path.write_text("x", encoding="utf-8")
            failures = _verify_paths_removed([str(test_path)])
            self.assertEqual(failures, [str(test_path)])

    def test_verify_paths_removed_passes_for_missing_paths(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            test_path = Path(temp_dir) / "gone.txt"
            test_path.write_text("x", encoding="utf-8")
            test_path.unlink()
            failures = _verify_paths_removed([str(test_path)])
            self.assertEqual(failures, [])


if __name__ == "__main__":
    unittest.main()
