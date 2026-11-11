import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dashboard.data import (
    FileEntry, UnstableFileError, load_records, read_payload, scan, select_entries,
)


class DataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, relative, payload=None, raw=None):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(raw if raw is not None else json.dumps(payload or {"score": 0.5}), encoding="utf-8")
        return path

    def test_both_layouts_numeric_iterations_and_flags(self):
        self.write("baseline@9000/result.json")
        self.write("baseline@10000/result.json")
        self.write("baseline/STAR", raw="")
        self.write("candidate/eval/iter_2000/result.json")
        self.write("candidate/eval/iter_20000/result.json")
        self.write("candidate/eval/iter_wrong/result.json")
        self.write("candidate/eval/iter_20000/FLAG", raw="")
        self.write("candidate/APPLE", raw="")
        self.write("candidate/eval/iter_20000/IGNORED", raw="")
        snapshot = scan(str(self.root), "result.json")
        self.assertFalse(snapshot.issues)
        self.assertEqual([(e.experiment, e.iteration) for e in snapshot.entries],
                         [("baseline", 9000), ("baseline", 10000), ("candidate", 2000), ("candidate", 20000)])
        self.assertEqual(snapshot.entries[0].flags, ("STAR",))
        self.assertEqual(snapshot.entries[-1].flags, ("FLAG", "APPLE"))
        selected = select_entries(snapshot.entries)
        self.assertEqual([e.iteration for e in selected], [10000, 20000])

    def test_multi_file_checkpoint_and_keyword_baseline_rules(self):
        for iteration in (2, 10):
            self.write(f"baseline@{iteration}/simple_a.json")
            self.write(f"baseline@{iteration}/simple_b.json")
            self.write(f"candidate@{iteration}/simple_a.json")
            self.write(f"hidden@{iteration}/simple_a.json")
        self.write("baseline/SUN", raw="")
        self.write("hidden/HIDE", raw="")
        entries = scan(str(self.root), "simple_*.json").entries
        selected = select_entries(entries, keywords=("candidate",))
        self.assertEqual([(e.experiment, e.iteration) for e in selected],
                         [("baseline", 10), ("baseline", 10), ("candidate", 10)])
        strict = select_entries(entries, keywords=("candidate",), keep_pinned=False)
        self.assertEqual(len(strict), 1)
        history = select_entries(entries, only_latest=False)
        self.assertEqual(len(history), 4)  # 2 baseline files + 2 candidate checkpoints
        all_history = select_entries(entries, keywords=("ALL",), only_latest=False, baseline_latest=False)
        self.assertEqual(len(all_history), 6)
        self.assertFalse(any(e.experiment == "hidden" for e in all_history))
        explicit = select_entries(entries, keywords=("not-present",), pinned_experiments=("candidate",))
        self.assertEqual({e.experiment for e in explicit}, {"baseline", "candidate"})

    def test_load_failure_isolation_and_metadata_precedence(self):
        self.write("good/eval/iter_10/result.json", {"score": 0.8})
        self.write("bad@10/result.json", raw="{\"score\":")
        self.write("good/job_config.json", {"root": True, "override": "root"})
        self.write("good/eval/iter_10/job_config.json", {"override": "checkpoint"})
        self.write("good/eval/iter_10/param.json", {"override": "param", "romaDetailUrl": "https://example.com"})
        records, issues = load_records(scan(str(self.root), "result.json").entries)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].payload, {"score": 0.8})
        self.assertEqual(records[0].metadata["override"], "param")
        self.assertTrue(records[0].metadata["root"])
        self.assertEqual(len(issues), 1)
        self.assertIn("bad@10", issues[0].path)

    def test_missing_or_invalid_metadata_does_not_discard_result(self):
        self.write("no_metadata@10/result.json")
        self.write("bad_metadata@10/result.json")
        self.write("bad_metadata@10/param.json", raw="[]")
        records, issues = load_records(scan(str(self.root), "result.json").entries)
        self.assertEqual(len(records), 2)
        self.assertEqual([record.metadata for record in records], [{}, {}])
        self.assertEqual(len(issues), 1)
        self.assertIn("元数据必须是 JSON 对象", issues[0].message)

    def test_stale_fingerprint_rejected_before_parse(self):
        path = self.write("changing@10/result.json")
        entry = scan(str(self.root), "result.json").entries[0]
        path.write_text('{"longer_score": 0.75}', encoding="utf-8")
        with self.assertRaises(UnstableFileError):
            read_payload(entry.path, entry.mtime_ns, entry.size)

    def test_file_changed_during_read_rejected(self):
        path = self.write("changing@10/result.json")
        entry = scan(str(self.root), "result.json").entries[0]
        actual_load = json.load

        def load_and_change(handle, **kwargs):
            content = actual_load(handle, **kwargs)
            path.write_text('{"longer_score": 0.99}', encoding="utf-8")
            return content

        with patch("dashboard.data.json.load", side_effect=load_and_change):
            with self.assertRaises(UnstableFileError):
                read_payload(entry.path, entry.mtime_ns, entry.size)

    def test_non_finite_json_is_rejected(self):
        self.write("bad@10/result.json", raw='{"value": NaN}')
        records, issues = load_records(scan(str(self.root), "result.json").entries)
        self.assertFalse(records)
        self.assertEqual(len(issues), 1)
        self.assertIn("非有限", issues[0].message)

    def test_missing_root_diagnostic_and_invalid_pattern(self):
        snapshot = scan(str(self.root / "missing"), "result.json")
        self.assertFalse(snapshot.entries)
        self.assertEqual(len(snapshot.issues), 1)
        with self.assertRaises(ValueError):
            scan(str(self.root), "../result.json")

    def test_unrelated_deep_tree_and_symlinks_are_not_walked(self):
        self.write("exp/eval/iter_10/result.json")
        self.write("exp/unrelated/deep/result.json")
        (self.root / "alias").symlink_to(self.root / "exp", target_is_directory=True)
        snapshot = scan(str(self.root), "result.json")
        self.assertEqual(len(snapshot.entries), 1)
        self.assertEqual(snapshot.entries[0].experiment, "exp")

    def test_filter_applied_before_json_load(self):
        self.write("exp@2/result.json", raw="bad json")
        self.write("exp@10/result.json", {"score": 1})
        selected = select_entries(scan(str(self.root), "result.json").entries)
        records, issues = load_records(selected)
        self.assertEqual(len(records), 1)
        self.assertFalse(issues)
        self.assertEqual(records[0].entry.iteration, 10)


if __name__ == "__main__":
    unittest.main()
