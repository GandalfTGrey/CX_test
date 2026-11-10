"""Characterize known loading failures in the existing dashboard, read-only.

These tests deliberately assert the CURRENT defects so they can be reproduced.
A passing result is evidence of the reported failure, not a repaired dashboard.
They need the original dashboard dependencies and either the sibling directory
``../optimized_dashboard`` or that source directory on PYTHONPATH. No real data
is read; temporary fixtures are removed on completion.
"""
import glob
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[2] / "optimized_dashboard"
if SOURCE.is_dir():
    sys.path.insert(0, str(SOURCE))

from dashboard import data
from dashboard.context import Context


class LoadingRegressionAudit(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="dashboard-audit-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)

    def assert_query_rejected_before_read(self, sizes, diagnostic):
        # Synthetic stat metadata exercises the actual admission branch without
        # creating large payloads or allocating their stated size.
        entries = tuple(
            data.FileEntry(
                "/fixture/exp_%d@1/pnc_eval.json" % index,
                "exp_%d" % index, 1, (), 1, size,
            )
            for index, size in enumerate(sizes)
        )
        context = Context("/fixture", "pnc")
        with patch("dashboard.context.cached_scan", return_value=(data.Snapshot(entries, ()), 0)), \
                patch("dashboard.context.load_records") as loader:
            records = context.records("pnc_eval.json")
        self.assertEqual(records, ())
        loader.assert_not_called()
        self.assertTrue(any(diagnostic in message for message in context.messages))

    def test_single_17_mib_file_discards_healthy_peer(self):
        self.assert_query_rejected_before_read([17 * 1024 * 1024, 2], "16 MiB")

    def test_nine_15_mib_files_exceed_total_budget_and_all_disappear(self):
        self.assert_query_rejected_before_read([15 * 1024 * 1024] * 9, "128 MiB")

    def test_2001_tiny_files_all_disappear(self):
        self.assert_query_rejected_before_read([2] * 2001, "2000")

    def make_symlink(self, link, target, is_directory=False):
        try:
            link.symlink_to(target, target_is_directory=is_directory)
        except (OSError, NotImplementedError) as error:
            self.skipTest("Symlink fixture unavailable on this host: %s" % error)

    def assert_old_glob_finds_but_new_scan_silently_ignores(self, root):
        self.assertEqual(len(glob.glob(str(root / "*" / "pnc_eval.json"))), 1)
        snapshot = data.scan(str(root), "pnc_eval.json")
        self.assertEqual(snapshot.entries, ())
        self.assertEqual(snapshot.issues, ())

    def test_symlink_experiment_is_silently_ignored(self):
        actual = self.base / "actual"
        actual.mkdir()
        (actual / "pnc_eval.json").write_text("{}", encoding="utf-8")
        root = self.base / "directory_link_root"
        root.mkdir()
        self.make_symlink(root / "exp@1000", actual, is_directory=True)
        self.assert_old_glob_finds_but_new_scan_silently_ignores(root)

    def test_symlink_result_is_silently_ignored(self):
        actual = self.base / "actual.json"
        actual.write_text("{}", encoding="utf-8")
        root = self.base / "file_link_root"
        (root / "exp@1000").mkdir(parents=True)
        self.make_symlink(root / "exp@1000" / "pnc_eval.json", actual)
        self.assert_old_glob_finds_but_new_scan_silently_ignores(root)

    def test_checkpoint_as_root_is_empty_without_layout_diagnostic(self):
        (self.base / "pnc_eval.json").write_text("{}", encoding="utf-8")
        snapshot = data.scan(str(self.base), "pnc_eval.json")
        self.assertEqual(snapshot.entries, ())
        self.assertEqual(snapshot.issues, ())

    def test_missing_root_has_an_explicit_issue(self):
        snapshot = data.scan(str(self.base / "absent"), "*.json")
        self.assertEqual(snapshot.entries, ())
        self.assertEqual(len(snapshot.issues), 1)

    def test_keyword_latest_selection_happens_after_full_discovery(self):
        root = self.base / "raw"
        for experiment in range(3):
            for checkpoint in range(4):
                folder = root / ("exp%d" % experiment) / "eval" / ("iter_%d" % checkpoint)
                folder.mkdir(parents=True)
                (folder / "pnc_eval.json").write_text("{}", encoding="utf-8")

        original_scandir = data.os.scandir
        original_exists = Path.exists
        counts = {"scandir": 0, "flag_exists": 0}

        def scan_count(path):
            counts["scandir"] += 1
            return original_scandir(path)

        def exists_count(path):
            if path.name in data.FLAG_NAMES:
                counts["flag_exists"] += 1
            return original_exists(path)

        with patch.object(data.os, "scandir", side_effect=scan_count), \
                patch.object(Path, "exists", exists_count):
            snapshot = data.scan(str(root), "pnc_eval.json")
            selected = data.select_entries(snapshot.entries, keywords=("exp0",), only_latest=True)

        self.assertEqual(len(snapshot.entries), 12)
        self.assertEqual(len(selected), 1)
        self.assertEqual(counts, {"scandir": 19, "flag_exists": 135})
        self.assertEqual((selected[0].experiment, selected[0].iteration), ("exp0", 3))


if __name__ == "__main__":
    unittest.main(verbosity=2)
