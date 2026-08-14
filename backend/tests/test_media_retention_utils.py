# Tests for app.services.media.retention – media cleanup and file utility helpers.

import tempfile
import unittest
from pathlib import Path

from app.services.media.retention import (
    _cleanup_empty_dirs,
    _delete_path,
    _dir_size_bytes,
    _iter_files,
    _to_gb,
    _verify_paths_removed,
)

class ToGbTests(unittest.TestCase):
    def test_one_gb(self):
        self.assertAlmostEqual(_to_gb(1024**3), 1.0)

    def test_zero(self):
        self.assertAlmostEqual(_to_gb(0), 0.0)

    def test_half_gb(self):
        self.assertAlmostEqual(_to_gb(1024**3 // 2), 0.5, places=1)

    def test_small_value(self):
        result = _to_gb(1024)
        self.assertAlmostEqual(result, round(1024 / (1024**3), 3))

class IterFilesTests(unittest.TestCase):
    def test_empty_dir(self):
        with tempfile.TemporaryDirectory() as td:
            result = _iter_files(Path(td))
            self.assertEqual(result, [])

    def test_finds_files(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "a.txt").write_text("hello")
            (Path(td) / "sub").mkdir()
            (Path(td) / "sub" / "b.txt").write_text("world")
            result = _iter_files(Path(td))
            self.assertEqual(len(result), 2)

    def test_nonexistent_dir(self):
        result = _iter_files(Path("/tmp/nonexistent_dir_abc123"))
        self.assertEqual(result, [])

class DirSizeBytesTests(unittest.TestCase):
    def test_empty_dir(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(_dir_size_bytes(Path(td)), 0)

    def test_counts_file_sizes(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "a.txt").write_bytes(b"x" * 100)
            (Path(td) / "b.txt").write_bytes(b"y" * 200)
            self.assertEqual(_dir_size_bytes(Path(td)), 300)

    def test_nonexistent_dir(self):
        self.assertEqual(_dir_size_bytes(Path("/tmp/nonexistent_dir_xyz789")), 0)

class CleanupEmptyDirsTests(unittest.TestCase):
    def test_removes_empty_subdirs(self):
        with tempfile.TemporaryDirectory() as td:
            empty_sub = Path(td) / "empty"
            empty_sub.mkdir()
            removed = _cleanup_empty_dirs(Path(td))
            self.assertEqual(removed, 1)
            self.assertFalse(empty_sub.exists())

    def test_keeps_non_empty_subdirs(self):
        with tempfile.TemporaryDirectory() as td:
            sub = Path(td) / "full"
            sub.mkdir()
            (sub / "data.txt").write_text("keep")
            removed = _cleanup_empty_dirs(Path(td))
            self.assertEqual(removed, 0)
            self.assertTrue(sub.exists())

class DeletePathTests(unittest.TestCase):
    def test_delete_file(self):
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "to_delete.txt"
            f.write_bytes(b"x" * 50)
            freed = _delete_path(f)
            self.assertEqual(freed, 50)
            self.assertFalse(f.exists())

    def test_delete_nonexistent(self):
        freed = _delete_path(Path("/tmp/nonexistent_file_abc"))
        self.assertEqual(freed, 0)

class VerifyPathsRemovedTests(unittest.TestCase):
    def test_nonexistent_passes(self):
        still_present = _verify_paths_removed(["/tmp/nonexistent_file_abc"])
        self.assertEqual(still_present, [])

    def test_existing_fails(self):
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "still_here.txt"
            f.write_text("hi")
            still_present = _verify_paths_removed([str(f)])
            self.assertEqual(len(still_present), 1)

if __name__ == "__main__":
    unittest.main()
