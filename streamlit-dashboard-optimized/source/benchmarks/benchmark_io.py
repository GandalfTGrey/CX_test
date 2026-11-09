#!/usr/bin/env python3
"""Deterministic local benchmark of JSON loading order, not production performance.

Run from anywhere:
    python benchmarks/benchmark_io.py
    python benchmarks/benchmark_io.py --output result.json

Both pipelines use the same scanner and validated JSON reader to isolate the
ordering change. The legacy model parses all results before selection; the new
pipeline selects before parsing. "Cold" means an empty Python application cache.
OS page caches are not cleared or measured. The warm case is a dictionary cache
simulation keyed by (path, mtime_ns, size), NOT a measurement of st.cache_data.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import platform
import sys
import tempfile
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dashboard.data import load_records, read_payload, scan, select_entries


EXPERIMENTS = 50
CHECKPOINTS = 20
KEYWORD = "experiment_017"
FILENAME = "result.json"


class InstrumentedReader:
    """Count uncached reader calls; each performs one open and one JSON parse."""

    def __init__(self, cache=None):
        self.cache = cache
        self.file_opens = 0
        self.json_parses = 0
        self.cache_hits = 0

    def __call__(self, path, mtime_ns, size):
        key = (path, mtime_ns, size)
        if self.cache is not None and key in self.cache:
            self.cache_hits += 1
            return self.cache[key]
        self.file_opens += 1
        self.json_parses += 1
        payload = read_payload(path, mtime_ns, size)
        if self.cache is not None:
            self.cache[key] = payload
        return payload

    def counts(self):
        return {"file_opens": self.file_opens, "json_parses": self.json_parses,
                "signature_cache_hits": self.cache_hits}


def generate_fixture(root):
    """Fixed data, moderate nested payload, no metadata, flags, or randomness."""
    total_bytes = 0
    for experiment in range(EXPERIMENTS):
        for checkpoint in range(1, CHECKPOINTS + 1):
            directory = root / f"experiment_{experiment:03d}@{checkpoint * 1000}"
            directory.mkdir()
            payload = {
                "experiment": f"experiment_{experiment:03d}",
                "iteration": checkpoint * 1000,
                "metrics": {
                    f"metric_{index:03d}": {
                        "value": round((experiment + checkpoint + index) / 201.0, 8),
                        "bins": [round((index + offset + checkpoint) / 257.0, 8)
                                 for offset in range(8)],
                        "unit": "fixture_only",
                    }
                    for index in range(128)
                },
            }
            encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            (directory / FILENAME).write_bytes(encoded)
            total_bytes += len(encoded)
    return total_bytes


def measure(root, *, legacy, cache=None):
    reader = InstrumentedReader(cache)
    started = perf_counter()
    snapshot = scan(str(root), FILENAME)
    if snapshot.issues:
        raise RuntimeError([asdict(issue) for issue in snapshot.issues])
    if legacy:
        all_records, issues = load_records(snapshot.entries, reader=reader)
        loaded_records_count = len(all_records)
        selected = select_entries(snapshot.entries, keywords=(KEYWORD,), only_latest=True,
                                  keep_pinned=False, baseline_latest=False)
        selected_paths = {entry.path for entry in selected}
        records = tuple(record for record in all_records if record.entry.path in selected_paths)
    else:
        selected = select_entries(snapshot.entries, keywords=(KEYWORD,), only_latest=True,
                                  keep_pinned=False, baseline_latest=False)
        records, issues = load_records(selected, reader=reader)
        loaded_records_count = len(records)
    elapsed = perf_counter() - started
    if issues:
        raise RuntimeError([asdict(issue) for issue in issues])
    return {
        "elapsed_ms": round(elapsed * 1000, 3),
        "discovered_result_files": len(snapshot.entries),
        "selected_result_files": len(selected),
        "loaded_result_records": loaded_records_count,
        "returned_selected_records": len(records),
        "selected_checkpoints": [record.entry.iteration for record in records],
        **reader.counts(),
    }, tuple((record.entry.experiment, record.entry.iteration, record.payload) for record in records)


def benchmark():
    with tempfile.TemporaryDirectory(prefix="dashboard-order-benchmark-") as directory:
        root = Path(directory)
        total_bytes = generate_fixture(root)
        legacy, legacy_records = measure(root, legacy=True)
        cache = {}
        optimized, optimized_records = measure(root, legacy=False, cache=cache)
        warm, warm_records = measure(root, legacy=False, cache=cache)
        if legacy_records != optimized_records or optimized_records != warm_records:
            raise AssertionError("Pipelines returned different selected data")
        entries = scan(str(root), FILENAME).entries
        latest_only = select_entries(entries, only_latest=True, baseline_latest=False, keep_pinned=False)
        keyword_only = select_entries(entries, keywords=(KEYWORD,), only_latest=False,
                                      baseline_latest=False, keep_pinned=False)
        return {
            "environment": {
                "python": platform.python_version(),
                "implementation": platform.python_implementation(),
                "platform": platform.platform(),
                "timer": "time.perf_counter",
            },
            "method": {
                "scope": "Local deterministic synthetic sample; no production speedup claim.",
                "legacy_model": "scan -> parse all result JSON -> latest and keyword selection",
                "optimized_model": "scan -> latest and keyword selection -> parse selected JSON",
                "shared_loader": "Both models use the same validated read_payload implementation.",
                "cold_definition": "Empty Python application cache; OS page cache is not cleared.",
                "warm_definition": "Dictionary signature cache simulation, NOT an st.cache_data measurement; discovery still runs.",
                "counter_definition": "Uncached instrumented_reader calls; fixture guarantees one file open and one JSON parse per call.",
                "timing_limits": "One pass per case, fixed order after fixture creation; includes directory discovery, stat checks, selection and loading. Not physical disk I/O latency.",
            },
            "fixture": {
                "experiments": EXPERIMENTS,
                "checkpoints_per_experiment": CHECKPOINTS,
                "result_files": EXPERIMENTS * CHECKPOINTS,
                "payload_bytes_total": total_bytes,
                "payload_bytes_mean": round(total_bytes / (EXPERIMENTS * CHECKPOINTS), 1),
                "layout": "ROOT/experiment_NNN@iteration/result.json",
                "keyword": KEYWORD,
                "metadata_files": 0,
                "pinned_experiments": 0,
            },
            "legacy_application_cache_cold": legacy,
            "optimized_application_cache_cold": optimized,
            "optimized_warm_signature_cache_simulation": warm,
            "selection_effect": {
                "unfiltered_files": len(entries),
                "latest_only_files": len(latest_only),
                "keyword_only_files_all_checkpoints": len(keyword_only),
                "keyword_and_latest_files": optimized["selected_result_files"],
                "cold_json_parses_saved": legacy["json_parses"] - optimized["json_parses"],
                "selected_payloads_equal": True,
            },
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Also write the exact JSON report to this path")
    args = parser.parse_args()
    result = json.dumps(benchmark(), ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(result + "\n", encoding="utf-8")
    print(result)


if __name__ == "__main__":
    main()
