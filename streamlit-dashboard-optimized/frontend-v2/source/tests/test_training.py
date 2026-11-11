import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from dashboard.training import parse_logs, read_training_logs, resolve_log_dir, speed_summary


class TrainingTests(unittest.TestCase):
    def test_all_three_legacy_formats(self):
        lines = "\n".join([
            "2026-01-01 00:00:00 train 1/2 100/1000 Fin",
            "[2026-01-01 00:01:40] [ 1/2 200/1000 Fin",
            "2026-01-01 00:05:00 Rank 0\ttrain:300/2000",
        ])
        self.assertEqual(parse_logs(lines), [(200, 1.0), (300, 2.0)])

    def test_epoch_converts_to_global_iteration(self):
        text = "\n".join([
            "2026-01-01 00:00:00 train 1/3 900/1000 Fin",
            "2026-01-01 00:03:20 train 2/3 100/1000 Fin",
        ])
        self.assertEqual(parse_logs(text), [(1100, 1.0)])

    def test_total_seconds_preserves_days(self):
        text = "\n".join([
            "2026-01-01 00:00:00 Rank 0\ttrain:100/1000",
            "2026-01-03 00:01:40 Rank 0\ttrain:200/1000",
        ])
        self.assertEqual(parse_logs(text), [(200, 1729.0)])

    def test_negative_time_is_rejected_without_negative_speed(self):
        text = "\n".join([
            "2026-01-01 00:01:40 Rank 0\ttrain:100/1000",
            "2026-01-01 00:00:00 Rank 0\ttrain:200/1000",
            "2026-01-01 00:05:00 Rank 0\ttrain:300/1000",
        ])
        self.assertEqual(parse_logs(text), [(300, 1.0)])

    def test_interval_filters_all_formats(self):
        text = "\n".join([
            "2026-01-01 00:00:00 Rank 0\ttrain:10/1000",
            "2026-01-01 00:00:40 Rank 0\ttrain:50/1000",
            "2026-01-01 00:01:40 Rank 0\ttrain:110/1000",
        ])
        self.assertEqual(parse_logs(text, 100), [(110, 1.0)])

    def test_empty_and_invalid_records(self):
        self.assertEqual(parse_logs(""), [])
        self.assertEqual(parse_logs("2026-99-01 00:00:00 Rank 0\ttrain:100/1000"), [])
        self.assertEqual(parse_logs("2026-01-01 00:00:00 train 0/2 100/1000 Fin"), [])
        self.assertEqual(parse_logs("2026-01-01 00:00:00 train 2/2 1001/1000 Fin"), [])
        for interval in [0, -1, 1.5, True]:
            with self.assertRaises(ValueError):
                parse_logs("", interval)

    def test_rotated_logs_merge_latest_duplicate_and_ignore_other_ranks(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            (directory / "b-Rank-0.log").write_text(
                "2026-01-01 00:03:20 Rank 0\ttrain:200/1000\n", encoding="utf-8")
            (directory / "a-Rank-0.log").write_text(
                "2026-01-01 00:00:00 Rank 0\ttrain:100/1000\n"
                "2026-01-01 00:01:40 Rank 0\ttrain:200/1000\n", encoding="utf-8")
            (directory / "Rank-01.log").write_text(
                "2026-01-01 00:10:00 Rank 0\ttrain:300/1000\n", encoding="utf-8")
            self.assertEqual(read_training_logs(root), [(200, 2.0)])
            self.assertEqual(resolve_log_dir(root), root)

    def test_direct_file_and_legacy_origin(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)
            text = ("2026-01-01 00:00:00 Rank 0\ttrain:100/1000\n"
                    "2026-01-01 00:01:40 Rank 0\ttrain:200/1000\n")
            for run in ["b", "a"]:
                directory = path / "ads" / run / "log"
                directory.mkdir(parents=True)
                (directory / "Rank-0.log").write_text(text, encoding="utf-8")
            target = path / "ads" / "a" / "log"
            self.assertEqual(resolve_log_dir(root), str(target))
            self.assertEqual(read_training_logs(root), [(200, 1.0)])
            self.assertEqual(read_training_logs(str(target / "Rank-0.log")), [(200, 1.0)])

    def test_failures_are_explicit(self):
        with self.assertRaises(ValueError):
            resolve_log_dir(" ")
        with self.assertRaises(ValueError):
            resolve_log_dir("obs://")
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(FileNotFoundError):
                resolve_log_dir(root)
            with self.assertRaises(NotADirectoryError):
                resolve_log_dir(str(Path(root) / "missing"))

    def test_local_read_does_not_import_moxing(self):
        with tempfile.TemporaryDirectory() as root:
            filename = Path(root) / "train.txt"
            filename.write_text("", encoding="utf-8")
            with patch("dashboard.training.importlib.import_module") as imported:
                self.assertEqual(read_training_logs(str(filename)), [])
                imported.assert_not_called()

    def test_missing_moxing_explains_remote_requirement(self):
        with patch("dashboard.training.importlib.import_module", side_effect=ImportError("missing")):
            with self.assertRaisesRegex(RuntimeError, "moxing"):
                read_training_logs("obs://bucket/run/log")

    def test_remote_read_only_adapter(self):
        content = ("2026-01-01 00:00:00 Rank 0\ttrain:100/1000\n"
                   "2026-01-01 00:01:40 Rank 0\ttrain:200/1000\n")
        directory = "obs://bucket/run/log"
        cloud = SimpleNamespace(
            is_directory=lambda path: path == directory,
            list_directory=lambda path: ["Rank-0.log"],
            File=lambda path, mode: io.StringIO(content),
        )
        with patch("dashboard.training.importlib.import_module", return_value=SimpleNamespace(file=cloud)):
            self.assertEqual(resolve_log_dir(directory), directory)
            self.assertEqual(read_training_logs(directory), [(200, 1.0)])

    def test_log_budget_rejects_input_and_direct_file(self):
        content = ("2026-01-01 00:00:00 Rank 0\ttrain:100/1000\n"
                   "2026-01-01 00:01:40 Rank 0\ttrain:200/1000\n")
        with tempfile.TemporaryDirectory() as root:
            filename = Path(root) / "train.txt"
            filename.write_text(content, encoding="utf-8")
            with patch("dashboard.training._MAX_LOG_BYTES", 50):
                with self.assertRaisesRegex(RuntimeError, "读取预算"):
                    parse_logs(content)
                with self.assertRaisesRegex(RuntimeError, "读取预算"):
                    read_training_logs(str(filename))

    def test_log_budget_is_cumulative_across_rotated_files(self):
        first = "2026-01-01 00:00:00 Rank 0\ttrain:100/1000\n"
        second = "2026-01-01 00:01:40 Rank 0\ttrain:200/1000\n"
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / "a-Rank-0.log").write_text(first, encoding="utf-8")
            (Path(root) / "b-Rank-0.log").write_text(second, encoding="utf-8")
            # Each file fits independently; the whole query must still fail.
            with patch("dashboard.training._MAX_LOG_BYTES", len(first.encode("utf-8")) + 1):
                with self.assertRaisesRegex(RuntimeError, "读取预算"):
                    read_training_logs(root)

    def test_local_and_remote_logs_stream_without_bulk_read(self):
        content = ("2026-01-01 00:00:00 Rank 0\ttrain:100/1000\n"
                   "2026-01-01 00:01:40 Rank 0\ttrain:200/1000\n")

        class IterationOnlyStream(io.StringIO):
            def read(self, *args, **kwargs):
                raise AssertionError("training logs must be consumed line by line")

        with patch("dashboard.training._ReadOnlyFiles.read", side_effect=AssertionError("bulk reader called")) as bulk:
            with tempfile.TemporaryDirectory() as root:
                filename = Path(root) / "Rank-0.log"
                filename.write_text(content, encoding="utf-8")
                with patch("dashboard.training.open", side_effect=lambda *args, **kwargs: IterationOnlyStream(content)):
                    self.assertEqual(read_training_logs(str(filename)), [(200, 1.0)])
                    self.assertEqual(read_training_logs(root), [(200, 1.0)])
            directory = "obs://bucket/run/log"
            cloud = SimpleNamespace(
                is_directory=lambda path: path == directory,
                list_directory=lambda path: ["Rank-0.log"],
                File=lambda *args: IterationOnlyStream(content),
            )
            with patch("dashboard.training.importlib.import_module", return_value=SimpleNamespace(file=cloud)):
                self.assertEqual(read_training_logs(directory), [(200, 1.0)])
            bulk.assert_not_called()

    def test_summary_filters_invalid_samples(self):
        self.assertIsNone(speed_summary([]))
        self.assertIsNone(speed_summary([(100, float("nan")), (200, -1), (300, float("inf"))]))
        self.assertEqual(speed_summary([(100, 1), (200, 3), (300, 100)]), 3.0)


if __name__ == "__main__":
    unittest.main()
