"""Read-only experiment discovery and JSON loading, independent of Streamlit.

Layouts: ROOT/experiment@12000/result.json, ROOT/experiment/result.json
(iteration 0), and ROOT/experiment/eval/iter_12000/result.json.
Metadata convention: optional job_config.json and param.json in the experiment
or checkpoint directory. Checkpoint metadata overrides experiment metadata;
param.json overrides job_config.json at the same level. Missing metadata is valid.
"""
from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatchcase
import json
import os
from pathlib import Path
import re
from typing import Callable, Iterable


TAG_NAMES = ("STAR", "SUN", "GALAXY", "FLAG", "APPLE", "GRAPE", "SAKURA", "STAR2")
FLAG_NAMES = TAG_NAMES + ("HIDE",)
_ITER = re.compile(r"iter_(\d+)\Z")
_FLAT = re.compile(r"(.+)@(\d+)\Z")


@dataclass(frozen=True)
class FileEntry:
    path: str
    experiment: str
    iteration: int
    flags: tuple[str, ...]
    mtime_ns: int
    size: int


@dataclass(frozen=True)
class Issue:
    path: str
    message: str


@dataclass(frozen=True)
class Record:
    entry: FileEntry
    payload: object
    metadata: dict


@dataclass(frozen=True)
class Snapshot:
    entries: tuple[FileEntry, ...]
    issues: tuple[Issue, ...]


class UnstableFileError(RuntimeError):
    """A file changed since discovery or while being read; retry after a refresh."""


def _children(directory: Path, issues: list[Issue]) -> list[os.DirEntry]:
    try:
        with os.scandir(directory) as iterator:
            return sorted(iterator, key=lambda item: item.name)
    except OSError as exc:
        issues.append(Issue(str(directory), f"无法读取目录: {exc}"))
        return []


def _flags(directories: Iterable[Path], issues: list[Issue]) -> tuple[str, ...]:
    result = set()
    for directory in dict.fromkeys(directories):
        for flag in FLAG_NAMES:
            try:
                if (directory / flag).exists():
                    result.add(flag)
            except OSError as exc:
                issues.append(Issue(str(directory / flag), f"无法读取标记: {exc}"))
    return tuple(flag for flag in FLAG_NAMES if flag in result)


def scan(root: str, pattern: str) -> Snapshot:
    """Discover matching files without parsing JSON or recursively walking a tree.

    ``pattern`` is a filename glob, never a path. Symlinked experiment/eval/
    checkpoint directories are not traversed, keeping discovery inside ROOT.
    Result-file symlinks are also ignored.
    """
    if not pattern or any(separator in pattern for separator in ("/", "\\")):
        raise ValueError("pattern 必须是文件名通配符，不能包含目录分隔符")
    base = Path(root).expanduser().resolve()
    issues: list[Issue] = []
    entries: list[FileEntry] = []
    flag_cache: dict[Path, tuple[str, ...]] = {}

    def flags_for(*directories: Path) -> tuple[str, ...]:
        found = set()
        for directory in dict.fromkeys(directories):
            if directory not in flag_cache:
                flag_cache[directory] = _flags((directory,), issues)
            found.update(flag_cache[directory])
        return tuple(flag for flag in FLAG_NAMES if flag in found)

    def collect(directory: Path, experiment: str, iteration: int, flags: tuple[str, ...], children=None):
        for item in children if children is not None else _children(directory, issues):
            if not fnmatchcase(item.name, pattern):
                continue
            try:
                if not item.is_file(follow_symlinks=False):
                    continue
                stat = item.stat(follow_symlinks=False)
                entries.append(FileEntry(str(directory / item.name), experiment, iteration,
                                         flags, stat.st_mtime_ns, stat.st_size))
            except OSError as exc:
                issues.append(Issue(str(directory / item.name), f"无法读取文件属性: {exc}"))

    for item in _children(base, issues):
        try:
            if not item.is_dir(follow_symlinks=False):
                continue
        except OSError as exc:
            issues.append(Issue(item.path, f"无法读取目录属性: {exc}"))
            continue
        directory = base / item.name
        flat = _FLAT.fullmatch(item.name)
        experiment = flat.group(1) if flat else item.name
        iteration = int(flat.group(2)) if flat else 0
        flags = flags_for(base / experiment, directory)
        children = _children(directory, issues)
        collect(directory, experiment, iteration, flags, children)
        # Flat cache checkpoints have no raw eval/iter_* child layout.
        if flat:
            continue
        evaluation = next((child for child in children if child.name == "eval"), None)
        if evaluation is None:
            continue
        try:
            if not evaluation.is_dir(follow_symlinks=False):
                continue
        except OSError as exc:
            issues.append(Issue(evaluation.path, f"无法读取目录属性: {exc}"))
            continue
        for checkpoint in _children(directory / "eval", issues):
            match = _ITER.fullmatch(checkpoint.name)
            if match is None:
                continue
            try:
                if not checkpoint.is_dir(follow_symlinks=False):
                    continue
            except OSError as exc:
                issues.append(Issue(checkpoint.path, f"无法读取目录属性: {exc}"))
                continue
            checkpoint_dir = Path(checkpoint.path)
            checkpoint_flags = flags_for(directory, checkpoint_dir)
            collect(checkpoint_dir, experiment, int(match.group(1)), checkpoint_flags)
    return Snapshot(tuple(sorted(entries, key=lambda e: (e.experiment, e.iteration, e.path))),
                    tuple(issues))


def _signature(stat: os.stat_result) -> tuple[int, int, int, int]:
    return stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_size


def _reject_constant(value: str):
    raise ValueError(f"JSON 包含非有限数值: {value}")


def read_payload(path: str, mtime_ns: int, size: int) -> object:
    """Read a complete strict JSON snapshot, rejecting stale/partially written files.

    The two discovery fields are intentional cache keys. The caller may cache
    this function by (path, mtime_ns, size); a changed file gets a new key.
    """
    before = os.stat(path)
    if (before.st_mtime_ns, before.st_size) != (mtime_ns, size):
        raise UnstableFileError("文件自扫描后已变化，请刷新后重试")
    with open(path, "r", encoding="utf-8-sig") as handle:
        opened = os.fstat(handle.fileno())
        if _signature(before) != _signature(opened):
            raise UnstableFileError("文件在打开时被替换，请刷新后重试")
        payload = json.load(handle, parse_constant=_reject_constant)
        after_read = os.fstat(handle.fileno())
    after_path = os.stat(path)
    if _signature(before) != _signature(after_read) or _signature(before) != _signature(after_path):
        raise UnstableFileError("文件正在写入或已被替换，请稍后刷新")
    return payload


def _metadata_directories(entry: FileEntry) -> tuple[Path, ...]:
    checkpoint = Path(entry.path).parent
    if checkpoint.parent.name == "eval" and _ITER.fullmatch(checkpoint.name):
        experiment = checkpoint.parent.parent
    else:
        experiment = checkpoint.parent / entry.experiment
    return tuple(dict.fromkeys((experiment, checkpoint)))


def load_records(
    entries: Iterable[FileEntry],
    reader: Callable[[str, int, int], object] = read_payload,
) -> tuple[tuple[Record, ...], tuple[Issue, ...]]:
    """Load independently: one bad result/metadata file never discards its peers.

    Metadata is read using its own current fingerprint. The supplied reader is
    used for result and metadata files, so Streamlit caching can wrap one API.
    """
    records: list[Record] = []
    issues: list[Issue] = []
    for entry in entries:
        try:
            payload = reader(entry.path, entry.mtime_ns, entry.size)
        except (OSError, ValueError, TypeError, RuntimeError) as exc:
            issues.append(Issue(entry.path, f"评测结果读取失败: {exc}"))
            continue
        metadata: dict = {}
        for directory in _metadata_directories(entry):
            for name in ("job_config.json", "param.json"):
                path = directory / name
                try:
                    stat = path.stat()
                except FileNotFoundError:
                    continue
                except OSError as exc:
                    issues.append(Issue(str(path), f"元数据读取失败: {exc}"))
                    continue
                try:
                    content = reader(str(path), stat.st_mtime_ns, stat.st_size)
                    if not isinstance(content, dict):
                        raise ValueError("元数据必须是 JSON 对象")
                    metadata.update(content)
                except (OSError, ValueError, TypeError, RuntimeError) as exc:
                    issues.append(Issue(str(path), f"元数据读取失败: {exc}"))
        records.append(Record(entry, payload, metadata))
    return tuple(records), tuple(issues)


def select_entries(
    entries: Iterable[FileEntry], keywords: Iterable[str] = (),
    only_latest: bool = True, baseline_latest: bool = True,
    pinned_experiments: Iterable[str] = (), keep_pinned: bool = True,
) -> tuple[FileEntry, ...]:
    """Filter before JSON loading; keep every matching file at a chosen checkpoint.

    Keywords use case-sensitive OR matching, preserving the source dashboard's
    behavior. ALL (case insensitive) disables keyword filtering. STAR/SUN and
    explicitly pinned experiments may be retained across keyword filtering.
    HIDE always wins. baseline_latest applies to STAR/SUN and explicit pins.
    """
    words = tuple(word.strip() for word in keywords if isinstance(word, str) and word.strip())
    apply_keywords = bool(words) and not any(word.upper() == "ALL" for word in words)
    pins = set(pinned_experiments)
    visible = tuple(entry for entry in entries if "HIDE" not in entry.flags)
    baselines = pins | {entry.experiment for entry in visible if {"STAR", "SUN"}.intersection(entry.flags)}
    selected = tuple(entry for entry in visible if
                     not apply_keywords or any(word in entry.experiment for word in words)
                     or (keep_pinned and entry.experiment in baselines))
    latest: dict[str, int] = {}
    for entry in selected:
        latest[entry.experiment] = max(latest.get(entry.experiment, -1), entry.iteration)
    return tuple(sorted((entry for entry in selected
                         if not (only_latest or (baseline_latest and entry.experiment in baselines))
                         or entry.iteration == latest[entry.experiment]),
                        key=lambda e: (e.experiment, e.iteration, e.path)))
