"""Read-only training-speed utilities, without Streamlit or cloud SDK side effects.

Directories may contain rotated Rank-0 logs. All matching files are read in sorted
filename order; duplicate iterations use the latest timestamp across all files.
Speed samples are then computed in iteration order. ``resolve_log_dir`` also
supports the legacy ``origin/ads/<run>/log`` layout: when several runs contain
logs, the first usable run in lexicographic order is chosen deterministically.
Cloud paths import moxing only when an ``obs://`` path is actually requested.
"""

from __future__ import annotations

import datetime as dt
import importlib
import math
import os
import posixpath
import re
from itertools import chain
from statistics import median

_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")
_TRAIN_EPOCH = re.compile(r"train\s*(\d+)/(\d+)\s+(\d+)/(\d+)")
_BARE_EPOCH = re.compile(r"\[\s*(\d+)/(\d+)\s+(\d+)/(\d+)\s+Fin")
_TRAIN_RANK = re.compile(r"Rank\s+0\s+train\s*:\s*(\d+)/(\d+)")
_RANK_ZERO_FILE = re.compile(r"Rank-0(?!\d)")
_MAX_LOG_BYTES = 128 * 1024 * 1024


def _validate_interval(min_iter_diff: int) -> None:
    if isinstance(min_iter_diff, bool) or not isinstance(min_iter_diff, int) or min_iter_diff < 1:
        raise ValueError("min_iter_diff 必须是大于 0 的整数")


def parse_logs(text: str, min_iter_diff: int = 100) -> list[tuple[int, float]]:
    """Parse three legacy timestamped formats and return (iteration, sec/iter).

    Supported records contain ``train epoch/epochs step/steps ... Fin``,
    ``[ epoch/epochs step/steps Fin`` or ``Rank 0\ttrain:iteration/total``.
    The first valid iteration is the anchor; later samples must advance by at
    least ``min_iter_diff``. Invalid dates, nonpositive elapsed times and malformed
    records are ignored. A repeated iteration uses its latest timestamp, which
    also makes merged rotated logs deterministic. Durations retain whole days.
    """
    _validate_interval(min_iter_diff)
    if not isinstance(text, str):
        raise TypeError("text 必须是字符串")
    return _parse_lines(text.splitlines(), min_iter_diff)


def _parse_lines(lines, min_iter_diff):
    timestamps: dict[int, dt.datetime] = {}
    processed_bytes = 0
    for line in lines:
        if isinstance(line, bytes):
            line = line.decode('utf-8', errors='replace')
        processed_bytes += len(line.encode('utf-8'))
        if processed_bytes > _MAX_LOG_BYTES:
            raise RuntimeError('日志超过单次 128 MiB 读取预算，请先选择较小的日志或裁剪副本。')
        timestamp_match = _TIMESTAMP.search(line)
        if timestamp_match is None:
            continue
        try:
            timestamp = dt.datetime.strptime(timestamp_match.group(), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        match = (_TRAIN_EPOCH.search(line) or _BARE_EPOCH.search(line)) if "Fin" in line else None
        if match is not None:
            epoch, epochs, step, steps = map(int, match.groups())
            if epoch < 1 or epochs < epoch or step < 0 or steps < 1 or step > steps:
                continue
            iteration = (epoch - 1) * steps + step
        else:
            match = _TRAIN_RANK.search(line)
            if match is None:
                continue
            iteration, total = map(int, match.groups())
            if total < 1 or iteration > total:
                continue
        previous = timestamps.get(iteration)
        if previous is None or timestamp > previous:
            timestamps[iteration] = timestamp

    points: list[tuple[int, float]] = []
    anchor: tuple[int, dt.datetime] | None = None
    for iteration, timestamp in sorted(timestamps.items()):
        if anchor is None:
            anchor = iteration, timestamp
            continue
        previous_iter, previous_time = anchor
        diff = iteration - previous_iter
        if diff < min_iter_diff:
            continue
        elapsed = (timestamp - previous_time).total_seconds()
        if elapsed <= 0:
            continue
        points.append((iteration, elapsed / diff))
        anchor = iteration, timestamp
    return points


class _ReadOnlyFiles:
    def __init__(self, path: str):
        self.remote = path.startswith("obs://")
        self.cloud = None
        if self.remote:
            try:
                self.cloud = importlib.import_module("moxing").file
            except (ImportError, AttributeError) as exc:
                raise RuntimeError("读取 obs:// 日志需要在当前环境安装并配置 moxing") from exc

    def join(self, path: str, *parts: str) -> str:
        return posixpath.join(path, *parts) if self.remote else os.path.join(path, *parts)

    def is_dir(self, path: str) -> bool:
        if self.remote:
            return bool(self.cloud.is_directory(path))
        return os.path.isdir(path)

    def names(self, path: str) -> list[str]:
        if self.remote:
            return sorted(self.cloud.list_directory(path))
        return sorted(os.listdir(path))

    def read(self, path: str) -> str:
        if self.remote:
            with self.cloud.File(path, "r") as stream:
                value = stream.read()
            return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value
        with open(path, "r", encoding="utf-8", errors="replace") as stream:
            return stream.read()

    def lines(self, path):
        """Keep at most one line plus the parsed timestamp index in memory."""
        if self.remote:
            with self.cloud.File(path, 'r') as stream:
                yield from stream
        else:
            with open(path, 'r', encoding='utf-8', errors='replace') as stream:
                yield from stream

    def rank_files(self, path: str) -> list[str]:
        return [
            self.join(path, name)
            for name in self.names(path)
            if _RANK_ZERO_FILE.search(name) and not self.is_dir(self.join(path, name))
        ]


def _clean_path(path: str) -> str:
    if not isinstance(path, str) or not path.strip():
        raise ValueError("日志路径不能为空")
    value = path.strip()
    if value.startswith("obs://"):
        if not value[len("obs://"):].strip("/"):
            raise ValueError("OBS 路径必须包含 bucket 名称")
        return value.rstrip("/")
    return os.path.abspath(os.path.expanduser(value))


def _resolve_log_dir(path: str, files: _ReadOnlyFiles) -> str:
    if not files.is_dir(path):
        raise NotADirectoryError(f"日志目录不存在或无法访问：{path}")
    if files.rank_files(path):
        return path
    ads = files.join(path, "ads")
    if files.is_dir(ads):
        for run in files.names(ads):
            candidate = files.join(ads, run, "log")
            if files.is_dir(candidate) and files.rank_files(candidate):
                return candidate
    raise FileNotFoundError(f"未找到 Rank-0 日志（支持直接日志目录或 ads/<run>/log）：{path}")


def resolve_log_dir(origin: str) -> str:
    """Resolve a direct log directory or the first usable legacy ads run.

    Empty paths, missing directories and directories without Rank-0 files raise
    explicit errors. Multiple Rank-0 files in the chosen directory are merged by
    ``read_training_logs``; multiple ads runs are not silently mixed together.
    """
    path = _clean_path(origin)
    return _resolve_log_dir(path, _ReadOnlyFiles(path))


def read_training_logs(path: str, min_iter_diff: int = 100) -> list[tuple[int, float]]:
    """Read a text file, a Rank-0 directory, or a legacy training output root.

    This function only reads files. It neither creates directories nor changes
    cloud authentication, writes caches, or exports/deletes training artifacts.
    """
    _validate_interval(min_iter_diff)
    path = _clean_path(path)
    files = _ReadOnlyFiles(path)
    if not files.is_dir(path):
        return _parse_lines(files.lines(path), min_iter_diff)
    directory = _resolve_log_dir(path, files)
    lines = chain.from_iterable(files.lines(filename) for filename in files.rank_files(directory))
    return _parse_lines(lines, min_iter_diff)


def speed_summary(points: list[tuple[int, float]]) -> float | None:
    """Return median positive finite sec/iter, or None when no samples exist."""
    values = [float(value) for _, value in points if math.isfinite(float(value)) and float(value) > 0]
    return float(median(values)) if values else None
