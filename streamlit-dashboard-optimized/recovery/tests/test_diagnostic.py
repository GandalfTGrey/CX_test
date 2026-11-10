"""Safety/behavior tests for the standalone diagnostic; standard library only.

All files are temporary synthetic fixtures. No NFS mount or dashboard dependency
is needed. Run with Python 3.8+: python -m unittest discover -s tests -p test_diagnostic.py -v
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "diagnose_loading.py"
SPEC = importlib.util.spec_from_file_location("diagnose_loading", str(SCRIPT))
DIAG = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DIAG)


class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="synthetic-diagnostic-")
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.root = self.folder / "private-root-name"
        self.root.mkdir()

    def args(self, **overrides):
        options = dict(root=str(self.root), pattern="metric.json", keyword=[],
                       all_checkpoints=False, no_keep_baselines=False,
                       timeout=10.0, max_directories=10000, max_entries=200000,
                       sample_files=6, sample_max_mib=8.0, output=None,
                       worker=False)
        options.update(overrides)
        return argparse.Namespace(**options)

    def file(self, relative, content='{"private_metric_value": 123.456}'):
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target

    def probe(self, **overrides):
        probe = DIAG.Probe(self.args(**overrides), lambda report: None)
        return probe, probe.run()

    def cli(self, *options):
        return subprocess.run([sys.executable, str(SCRIPT), "--root", str(self.root),
                               "--pattern", "metric.json"] + list(options),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True, timeout=12)

    def test_discovers_flat_raw_and_iteration_zero_layouts(self):
        self.file("flat@120/metric.json")
        self.file("raw/eval/iter_240/metric.json")
        self.file("direct/metric.json")
        self.file("raw/eval/iter_240/unrelated.json")
        self.file("raw/eval/invalid_999/metric.json")
        self.file("outside/layout/deep/metric.json")
        probe, report = self.probe()
        self.assertEqual("complete", report["status"])
        self.assertEqual({("flat", 120), ("raw", 240), ("direct", 0)},
                         {(entry[0], entry[1]) for entry in probe.entries})
        self.assertEqual(3, report["selection"]["files"])
        self.assertEqual(3, len(report["samples"]))

    def test_latest_all_baseline_hide_and_keyword_rules(self):
        for experiment in ("wanted", "other", "baseline", "hidden"):
            for iteration in (10, 20):
                self.file("{}/eval/iter_{}/metric.json".format(experiment, iteration))
        self.file("baseline/STAR", "")
        self.file("hidden/HIDE", "")
        probe, _ = self.probe(keyword=["wanted"])
        self.assertEqual({("wanted", 20), ("baseline", 20)},
                         {(entry[0], entry[1]) for entry in probe.select(True)})
        probe, _ = self.probe(keyword=["wanted"], all_checkpoints=True)
        self.assertEqual({("wanted", 10), ("wanted", 20), ("baseline", 20)},
                         {(entry[0], entry[1]) for entry in probe.select(True)})
        _, report = self.probe(keyword=["wanted"], no_keep_baselines=True)
        self.assertEqual(1, report["selection"]["files"])
        _, report = self.probe(keyword=["aLl"], all_checkpoints=True)
        self.assertEqual(5, report["selection"]["files"])

    def test_keyword_whitespace_matches_current_loader(self):
        self.file("wanted@20/metric.json")
        self.file("other@20/metric.json")
        _, trimmed = self.probe(keyword=[" wanted "])
        _, blank = self.probe(keyword=["  "])
        self.assertEqual(1, trimmed["selection"]["files"])
        self.assertEqual(2, blank["selection"]["files"])

    def test_budget_rejection_detected_without_reading_large_file(self):
        large = self.file("large@10/metric.json", "")
        with large.open("r+b") as stream:
            stream.truncate(17 * DIAG.MIB)
        self.file("normal@10/metric.json", "{}")
        _, report = self.probe()
        self.assertEqual(2, report["selection"]["files"])
        self.assertTrue(report["selection"]["would_current_loader_reject_all"])
        self.assertTrue(report["selection"]["current_loader_rejection_rules"]["any_file_over_16_mib"])
        self.assertEqual(1, report["counts"]["files_above_sample_read_cap"])
        self.assertEqual(["valid_json"], [sample["status"] for sample in report["samples"]])
        self.assertIn("CURRENT_LOADER_REJECTS_ENTIRE_QUERY_BY_BUDGET", report["findings"])

    def test_total_budget_and_duplicate_identity(self):
        for number in range(9):
            path = self.file("large{}@10/metric.json".format(number), "")
            with path.open("r+b") as stream:
                stream.truncate(15 * DIAG.MIB)
        self.file("duplicate@10/metric.json", "{}")
        self.file("duplicate/eval/iter_10/metric.json", "{}")
        _, report = self.probe(sample_files=0)
        selection = report["selection"]
        self.assertEqual(1, selection["duplicate_identities_excluded"])
        self.assertEqual(9, selection["files"])
        self.assertTrue(selection["current_loader_rejection_rules"]["total_bytes_over_128_mib"])
        self.assertFalse(selection["current_loader_rejection_rules"]["any_file_over_16_mib"])

    def test_file_count_budget_on_synthetic_entries(self):
        probe = DIAG.Probe(self.args(), lambda report: None)
        probe.entries = [("experiment{}".format(number), 1, "/unused/result.json", 2, 0, set())
                         for number in range(2001)]
        probe.select(True)
        self.assertTrue(probe.report["selection"]["current_loader_rejection_rules"]["file_count_over_2000"])

    def test_symlinks_are_counted_and_not_traversed(self):
        target = self.file("real@10/metric.json", "{}")
        try:
            os.symlink(str(target.parent), str(self.root / "linked@10"), target_is_directory=True)
            destination = self.root / "filelink@10"
            destination.mkdir()
            os.symlink(str(target), str(destination / "metric.json"))
        except (OSError, NotImplementedError) as exc:
            self.skipTest("symlink unavailable: {}".format(type(exc).__name__))
        _, report = self.probe()
        self.assertEqual(1, report["counts"]["symlinks_skipped"])
        self.assertEqual(1, report["counts"]["matching_file_symlinks_skipped"])
        self.assertEqual(1, report["selection"]["files"])

    def test_root_level_result_is_explained(self):
        self.file("metric.json", "{}")
        _, report = self.probe()
        self.assertEqual(0, report["selection"]["files"])
        self.assertEqual(1, report["counts"]["matching_files_at_root_ignored_by_current_loader"])
        self.assertIn("RESULT_FILES_AT_ROOT_CURRENT_LOADER_DOES_NOT_INCLUDE", report["findings"])

    def test_missing_root_is_not_reported_as_complete(self):
        _, report = self.probe(root=str(self.root / "does-not-exist"))
        self.assertNotEqual("complete", report["status"])
        self.assertFalse(report["selection"]["complete_discovery"])
        self.assertTrue(any(key.startswith("scandir:FileNotFoundError") for key in report["errors"]))

    def test_bad_json_nonfinite_and_encoding_do_not_hide_valid_peer(self):
        self.file("valid@1/metric.json", "{}")
        self.file("invalid@1/metric.json", "{not valid}")
        self.file("nonfinite@1/metric.json", '{"v": NaN}')
        invalid = self.file("encoding@1/metric.json", "")
        invalid.write_bytes(b"\xff\xfe")
        _, report = self.probe()
        states = {sample["status"] for sample in report["samples"]}
        self.assertEqual({"valid_json", "JSONDecodeError", "ValueError", "UnicodeDecodeError"}, states)
        self.assertEqual(4, report["selection"]["files"])
        self.assertEqual(3, sum(report["errors"].values()))

    def test_reports_omit_paths_names_keywords_and_business_content(self):
        self.file("sensitive-experiment@1/metric.json", '{"top_secret_field": "sensitive_value_abcdef"}')
        _, report = self.probe(keyword=["sensitive-experiment"])
        serial = json.dumps(report, ensure_ascii=False)
        for value in (str(self.root), "private-root-name", "sensitive-experiment",
                      "top_secret_field", "sensitive_value_abcdef", "metric.json"):
            self.assertNotIn(value, serial)
        self.assertEqual(1, report["scope"]["keyword_count"])

    def test_directory_budget_marks_partial_totals(self):
        self.file("first@1/metric.json", "{}")
        _, report = self.probe(max_directories=1)
        self.assertEqual("partial", report["status"])
        self.assertEqual("directory_budget", report["stopped_reason"])
        self.assertFalse(report["selection"]["complete_discovery"])
        self.assertIsNone(report["selection"]["would_current_loader_reject_all"])

    def test_cli_timeout_saves_partial_json(self):
        output = self.folder / "partial.json"
        result = self.cli("--timeout", "0.000001", "--output", str(output))
        self.assertEqual(0, result.returncode, result.stderr)
        report = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual("partial", report["status"])
        self.assertEqual("parent_wall_clock_timeout", report["stopped_reason"])
        self.assertIn("PARTIAL_REPORT_DO_NOT_INTERPRET_AS_COMPLETE_DIRECTORY_TOTALS", report["findings"])

    def test_cli_does_not_overwrite_output_or_modify_inputs(self):
        target = self.file("private@1/metric.json", "{}")
        before = (target.read_bytes(), target.stat().st_mtime_ns)
        output = self.folder / "report.json"
        result = self.cli("--output", str(output))
        self.assertEqual(0, result.returncode, result.stderr)
        report_bytes = output.read_bytes()
        self.assertEqual("complete", json.loads(report_bytes)["status"])
        second = self.cli("--output", str(output))
        self.assertNotEqual(0, second.returncode)
        self.assertEqual(report_bytes, output.read_bytes())
        self.assertEqual(before, (target.read_bytes(), target.stat().st_mtime_ns))

    def test_cli_rejects_report_inside_source_directory(self):
        self.file("private@1/metric.json", "{}")
        output = self.root / "report.json"
        result = self.cli("--output", str(output))
        self.assertNotEqual(0, result.returncode)
        self.assertFalse(output.exists())

    def test_entry_budget_never_claims_complete_totals(self):
        self.file("first@1/metric.json", "{}")
        self.file("second@1/metric.json", "{}")
        _, report = self.probe(max_entries=1)
        self.assertEqual("partial", report["status"])
        self.assertEqual("entry_budget", report["stopped_reason"])
        self.assertFalse(report["selection"]["complete_discovery"])
        self.assertIsNone(report["selection"]["would_current_loader_reject_all"])


if __name__ == "__main__":
    unittest.main()
