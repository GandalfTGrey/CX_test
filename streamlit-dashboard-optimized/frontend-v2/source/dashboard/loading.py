"""Bounded payload cache and background page reads; no Streamlit calls here."""
from collections import OrderedDict
import os
from pathlib import Path
import threading
import time

from .data import Issue, Record, read_payload, _metadata_directories

_cache = OrderedDict()
_cache_size = 0
_cache_lock = threading.Lock()
_jobs = OrderedDict()
_jobs_lock = threading.Lock()
_channels = threading.Semaphore(4)
MAX_FILE = 16 * 1024 * 1024
PAGE_BYTES = 32 * 1024 * 1024
CACHE_BYTES = 32 * 1024 * 1024


def payload(path, mtime, size):
    global _cache_size
    key = (path, mtime, size)
    with _cache_lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]
    if size > MAX_FILE:
        raise ValueError('单文件超过16 MiB，已单独跳过；请缩小文件或提取所需指标，其他结果继续展示')
    value = read_payload(path, mtime, size)
    with _cache_lock:
        # Another worker may have cached the same fingerprint while this read ran.
        if key not in _cache:
            _cache[key] = value
            _cache_size += size
        while _cache_size > CACHE_BYTES and _cache:
            old, _ = _cache.popitem(last=False)
            _cache_size -= old[2]
    return value


class Job:
    def __init__(self, entries, metadata):
        self.entries = entries
        self.metadata = metadata
        self.records = ()
        self.issues = []
        self.done = False
        self.cancelled = threading.Event()
        self.count = 0
        self.bytes = 0
        self.started = time.monotonic()
        self.phase = '等待读取通道'
        threading.Thread(target=self.run, daemon=True).start()

    def run(self):
        acquired = False
        try:
            while not acquired:
                if self.cancelled.is_set():
                    return
                acquired = _channels.acquire(timeout=.2)
            self.phase = '读取所选指标'
            records = []
            metadata_values = {}
            for entry in self.entries:
                if self.cancelled.is_set():
                    break
                try:
                    if entry.size > MAX_FILE:
                        raise ValueError('单文件超过16 MiB，已单独跳过，其他结果继续展示')
                    if self.bytes + entry.size > PAGE_BYTES:
                        raise ValueError('本页32 MiB读取预算已用尽，该文件未读取；请减小每页数量')
                    value = payload(entry.path, entry.mtime_ns, entry.size)
                    self.bytes += entry.size
                    meta = {}
                    for directory in _metadata_directories(entry):
                        for name in ('job_config.json', 'param.json'):
                            path = str(directory / name)
                            signature = self.metadata.get(path)
                            if signature is None:
                                continue
                            if path not in metadata_values:
                                try:
                                    content = payload(path, *signature)
                                    if not isinstance(content, dict):
                                        raise ValueError('元数据必须为JSON对象')
                                    metadata_values[path] = content
                                except (OSError, ValueError, RuntimeError, TypeError) as error:
                                    self.issues.append(Issue(path, str(error)))
                                    metadata_values[path] = {}
                            meta.update(metadata_values[path])
                    records.append(Record(entry, value, meta))
                except (OSError, ValueError, RuntimeError, TypeError, MemoryError, RecursionError) as error:
                    self.issues.append(Issue(entry.path, '{}: {}'.format(type(error).__name__, error)))
                finally:
                    self.count += 1
            self.records = tuple(records)
        except Exception as error:
            self.issues.append(Issue('', '{}: {}'.format(type(error).__name__, error)))
        finally:
            if acquired:
                _channels.release()
            self.done = True


def get_job(key, entries, metadata):
    with _jobs_lock:
        if key not in _jobs:
            _jobs[key] = Job(entries, metadata)
        _jobs.move_to_end(key)
        # Never silently discard a running request; signal cancellation on eviction.
        while len(_jobs) > 24:
            _, old = _jobs.popitem(last=False)
            if not old.done:
                old.cancelled.set()
        return _jobs[key]
