#!/usr/bin/env python3
"""Bounded, read-only loading diagnosis. Python 3.8+, standard library only.

No data is uploaded. Reports contain counts, sizes, timings and error codes, not
paths, experiment names, keywords or JSON values. This models the current loader's
two directory layouts and default STAR/SUN pins; it does not modify the dashboard.
"""
import argparse
from collections import Counter
from contextlib import contextmanager
import fnmatch
import importlib.metadata
import json
import os
from pathlib import Path
import re
import selectors
import subprocess
import sys
import time

MIB = 1024 * 1024
FLAGS = ('STAR', 'SUN', 'GALAXY', 'FLAG', 'APPLE', 'GRAPE', 'SAKURA', 'STAR2', 'HIDE')


class BudgetReached(Exception):
    pass


class Probe:
    def __init__(self, args, emit):
        self.args, self.emit = args, emit
        self.started = time.monotonic()
        self.last_emit = 0
        self.entries = []
        self.flag_cache = {}
        self.report = {'schema_version': 1, 'status': 'running', 'phase': '准备',
                       'python': sys.version.split()[0], 'packages': {},
                       'filesystem_type': 'unknown', 'counts': {}, 'timings': {},
                       'errors': {}, 'selection': {}, 'samples': [], 'findings': [],
                       'scope': {'latest': not args.all_checkpoints,
                                 'keep_baselines': not args.no_keep_baselines,
                                 'keyword_count': len(args.keyword),
                                 'timeout_seconds': args.timeout,
                                 'max_directories': args.max_directories,
                                 'max_entries': args.max_entries,
                                 'sample_files': args.sample_files,
                                 'sample_max_mib': args.sample_max_mib},
                       'limits': ['Fixed-depth discovery, no symlink traversal.',
                                  'No physical disk cache was cleared.',
                                  'No payload/schema values or paths are included.',
                                  'JSON read/parse are sampled; this is not browser rendering time.']}

    def count(self, key, amount=1):
        counts = self.report['counts']
        counts[key] = counts.get(key, 0) + amount

    def error(self, operation, exc):
        key = '{}:{}:{}'.format(operation, type(exc).__name__, getattr(exc, 'errno', None))
        errors = self.report['errors']
        errors[key] = errors.get(key, 0) + 1

    @contextmanager
    def timed(self, name):
        started = time.perf_counter()
        try:
            yield
        finally:
            elapsed = (time.perf_counter() - started) * 1000
            metric = self.report['timings'].setdefault(name, {'calls': 0, 'total_ms': 0, 'max_ms': 0})
            metric['calls'] += 1
            metric['total_ms'] = round(metric['total_ms'] + elapsed, 3)
            metric['max_ms'] = round(max(metric['max_ms'], elapsed), 3)

    def progress(self, force=False):
        self.report['elapsed_seconds'] = round(time.monotonic() - self.started, 3)
        now = time.monotonic()
        if force or now - self.last_emit > 0.5:
            self.emit(self.report)
            self.last_emit = now
        if now - self.started > self.args.timeout:
            raise BudgetReached('time_budget')

    def children(self, directory):
        if self.report['counts'].get('directories_listed', 0) >= self.args.max_directories:
            raise BudgetReached('directory_budget')
        self.count('directories_listed')
        self.progress()
        items = []
        try:
            with self.timed('directory_enumeration'), os.scandir(directory) as stream:
                for item in stream:
                    self.count('directory_entries_seen')
                    if self.report['counts']['directory_entries_seen'] > self.args.max_entries:
                        raise BudgetReached('entry_budget')
                    items.append(item)
                    self.progress()
        except OSError as exc:
            self.error('scandir', exc)
        return items

    def is_dir(self, entry):
        with self.timed('entry_type_checks'):
            try:
                if entry.is_symlink():
                    self.count('symlinks_skipped')
                    return False
                return entry.is_dir(follow_symlinks=False)
            except OSError as exc:
                self.error('entry_type', exc)
                return False

    def flags(self, directories):
        found = set()
        for directory in dict.fromkeys(directories):
            if directory not in self.flag_cache:
                flags = set()
                for flag in FLAGS:
                    self.progress()
                    self.count('flag_probes')
                    try:
                        with self.timed('flag_exists_checks'):
                            exists = os.path.exists(os.path.join(directory, flag))
                        if exists:
                            flags.add(flag)
                    except OSError as exc:
                        self.error('flag', exc)
                self.flag_cache[directory] = flags
            found.update(self.flag_cache[directory])
        return found

    def collect(self, items, experiment, iteration, flags):
        for item in items:
            if not fnmatch.fnmatchcase(item.name, self.args.pattern):
                continue
            try:
                if item.is_symlink():
                    self.count('matching_file_symlinks_skipped')
                    continue
                with self.timed('result_stat_checks'):
                    if not item.is_file(follow_symlinks=False):
                        continue
                    stat = item.stat(follow_symlinks=False)
                self.entries.append((experiment, iteration, item.path, stat.st_size, stat.st_mtime_ns, flags))
                self.count('matching_files_discovered')
            except OSError as exc:
                self.error('result_stat', exc)
            self.progress()

    def discover(self):
        root = os.path.abspath(os.path.expanduser(self.args.root))
        self.report['phase'] = '目录枚举与元数据检查'
        self.progress(True)
        for entry in self.children(root):
            if fnmatch.fnmatchcase(entry.name, self.args.pattern):
                try:
                    if entry.is_file():
                        self.count('matching_files_at_root_ignored_by_current_loader')
                except OSError as exc:
                    self.error('root_file_type', exc)
            if not self.is_dir(entry):
                continue
            flat = re.fullmatch(r'(.+)@(\d+)', entry.name)
            experiment = flat.group(1) if flat else entry.name
            iteration = int(flat.group(2)) if flat else 0
            flags = self.flags((os.path.join(root, experiment), entry.path))
            items = self.children(entry.path)
            self.collect(items, experiment, iteration, flags)
            if flat:
                self.count('flat_checkpoint_directories')
                continue
            evaluation = next((item for item in items if item.name == 'eval'), None)
            if evaluation is None or not self.is_dir(evaluation):
                continue
            for checkpoint in self.children(evaluation.path):
                match = re.fullmatch(r'iter_(\d+)', checkpoint.name)
                if not match or not self.is_dir(checkpoint):
                    continue
                self.count('raw_checkpoint_directories')
                flags = self.flags((entry.path, checkpoint.path))
                self.collect(self.children(checkpoint.path), experiment, int(match.group(1)), flags)

    def select(self, complete):
        words = [word.strip() for word in self.args.keyword if word.strip()]
        visible = [entry for entry in self.entries if 'HIDE' not in entry[5]]
        baselines = {entry[0] for entry in visible if {'STAR', 'SUN'} & entry[5]}
        selected = [entry for entry in visible if not words or 'ALL' in [w.upper() for w in words]
                    or any(word in entry[0] for word in words)
                    or (not self.args.no_keep_baselines and entry[0] in baselines)]
        latest = {}
        for entry in selected:
            latest[entry[0]] = max(latest.get(entry[0], -1), entry[1])
        selected = [entry for entry in selected
                    if (self.args.all_checkpoints and entry[0] not in baselines)
                    or entry[1] == latest[entry[0]]]
        identities = Counter((entry[0], entry[1], os.path.basename(entry[2])) for entry in selected)
        conflicts = {key for key, count in identities.items() if count > 1}
        selected = [entry for entry in selected
                    if (entry[0], entry[1], os.path.basename(entry[2])) not in conflicts]
        total = sum(entry[3] for entry in selected)
        limits = {'file_count_over_2000': len(selected) > 2000,
                  'total_bytes_over_128_mib': total > 128 * MIB,
                  'any_file_over_16_mib': any(entry[3] > 16 * MIB for entry in selected)}
        self.report['selection'] = {'complete_discovery': complete, 'files': len(selected),
                                    'experiments': len({entry[0] for entry in selected}),
                                    'bytes': total, 'max_file_bytes': max([entry[3] for entry in selected] or [0]),
                                    'duplicate_identities_excluded': len(conflicts),
                                    'current_loader_rejection_rules': limits,
                                    'would_current_loader_reject_all': any(limits.values()) if complete else None}
        return selected

    def sample(self, entries):
        self.report['phase'] = '抽样读取与 JSON 解析'
        self.progress(True)
        cap = int(self.args.sample_max_mib * MIB)
        eligible = sorted((entry for entry in entries if entry[3] <= cap), key=lambda entry: entry[3])
        self.count('files_above_sample_read_cap', len(entries) - len(eligible))
        count = min(self.args.sample_files, len(eligible))
        indices = sorted({round(i * (len(eligible) - 1) / max(1, count - 1)) for i in range(count)})
        for index in indices:
            entry = eligible[index]
            sample = {'number': len(self.report['samples']) + 1, 'bytes': entry[3], 'status': 'pending'}
            self.report['samples'].append(sample)
            self.progress(True)
            try:
                with self.timed('sample_file_read'):
                    before = os.stat(entry[2])
                    with open(entry[2], 'rb') as stream:
                        opened = os.fstat(stream.fileno())
                        raw = stream.read(cap + 1)
                        closed = os.fstat(stream.fileno())
                    after = os.stat(entry[2])
                sig = lambda stat: (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
                if len(raw) > cap:
                    sample['status'] = 'grew_above_sample_cap'
                    continue
                if sig(before) != sig(opened) or sig(opened) != sig(closed) or sig(closed) != sig(after):
                    sample['status'] = 'changed_during_read'
                    continue
                if (before.st_size, before.st_mtime_ns) != (entry[3], entry[4]):
                    sample['status'] = 'changed_since_discovery'
                    continue
                def reject_constant(value):
                    raise ValueError('Non-finite JSON constant')
                with self.timed('sample_json_decode_and_parse'):
                    payload = json.loads(raw.decode('utf-8-sig'), parse_constant=reject_constant)
                del payload, raw
                sample['status'] = 'valid_json'
            except (OSError, ValueError, RecursionError, MemoryError) as exc:
                sample['status'] = type(exc).__name__
                self.error('sample', exc)
            self.progress(True)

    def environment(self):
        for name in ('streamlit', 'streamlit-aggrid', 'pandas', 'numpy'):
            try:
                self.report['packages'][name] = importlib.metadata.version(name)
            except importlib.metadata.PackageNotFoundError:
                self.report['packages'][name] = 'not_installed'
        try:
            root = os.path.abspath(os.path.expanduser(self.args.root))
            best = 0
            with open('/proc/self/mountinfo', encoding='utf-8') as stream:
                for line in stream:
                    fields = line.split()
                    split = fields.index('-')
                    mount = fields[4].replace('\\040', ' ').replace('\\134', '\\')
                    if os.path.commonpath((root, mount)) == mount and len(mount) > best:
                        best = len(mount)
                        self.report['filesystem_type'] = fields[split + 1]
        except (OSError, ValueError, IndexError):
            pass

    def run(self):
        self.progress(True)
        self.environment()
        complete = False
        try:
            self.discover()
            complete = not bool(self.report['errors'])
            entries = self.select(complete)
            self.sample(entries)
            self.report['status'] = 'complete' if complete else 'partial'
            if not complete:
                self.report['stopped_reason'] = 'filesystem_errors_during_discovery'
        except BudgetReached as exc:
            self.report['status'] = 'partial'
            self.report['stopped_reason'] = str(exc)
            self.report['last_observed_phase'] = self.report['phase']
            if not self.report['selection']:
                self.select(complete)
        except Exception as exc:
            self.error('probe', exc)
            self.report['status'] = 'failed'
        add_findings(self.report)
        self.report['phase'] = '完成' if self.report['status'] == 'complete' else '部分结果'
        self.report['elapsed_seconds'] = round(time.monotonic() - self.started, 3)
        self.emit(self.report)
        return self.report


def add_findings(report):
    counts, selection = report.get('counts', {}), report.get('selection', {})
    findings = []
    if selection.get('would_current_loader_reject_all'):
        findings.append('CURRENT_LOADER_REJECTS_ENTIRE_QUERY_BY_BUDGET')
    if counts.get('symlinks_skipped') or counts.get('matching_file_symlinks_skipped'):
        findings.append('SYMLINKS_PRESENT_AND_SKIPPED_BY_CURRENT_LAYOUT_POLICY')
    if counts.get('matching_files_at_root_ignored_by_current_loader'):
        findings.append('RESULT_FILES_AT_ROOT_CURRENT_LOADER_DOES_NOT_INCLUDE')
    if not counts.get('matching_files_discovered'):
        findings.append('NO_MATCH_DISCOVERED_CHECK_ROOT_PATTERN_LAYOUT_OR_ACCESS')
    if report.get('status') != 'complete':
        findings.append('PARTIAL_REPORT_DO_NOT_INTERPRET_AS_COMPLETE_DIRECTORY_TOTALS')
    if report.get('errors'):
        findings.append('FILESYSTEM_OR_JSON_ERRORS_SEE_ERROR_CODES')
    report['findings'] = findings


def supervise(args):
    # A subprocess allows the parent to keep giving feedback even during an NFS
    # syscall. Linux uninterruptible I/O may postpone worker termination.
    child = subprocess.Popen([sys.executable, '-u', os.path.abspath(__file__)] + sys.argv[1:] + ['--worker'],
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    selector = selectors.DefaultSelector()
    selector.register(child.stdout, selectors.EVENT_READ)
    started = last_print = time.monotonic()
    latest = {'schema_version': 1, 'status': 'running', 'phase': '启动诊断进程', 'counts': {}}
    buffer = b''
    try:
        while selector.get_map():
            now = time.monotonic()
            if now - started >= args.timeout:
                latest['status'] = 'partial'
                latest['stopped_reason'] = 'parent_wall_clock_timeout'
                latest['last_observed_phase'] = latest.get('phase')
                break
            for key, _ in selector.select(timeout=min(0.3, args.timeout - (now - started))):
                chunk = os.read(key.fd, 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                buffer += chunk
                while b'\n' in buffer:
                    line, buffer = buffer.split(b'\n', 1)
                    latest = json.loads(line.decode('utf-8'))
            if now - last_print >= 1:
                counts = latest.get('counts', {})
                print('[{:.0f}s] {} · 目录 {} · 匹配文件 {} · 标记检查 {}'.format(
                    now - started, latest.get('phase'), counts.get('directories_listed', 0),
                    counts.get('matching_files_discovered', 0), counts.get('flag_probes', 0)), flush=True)
                last_print = now
    except KeyboardInterrupt:
        latest['status'] = 'partial'
        latest['stopped_reason'] = 'user_cancelled'
    finally:
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                child.kill()
        child.stdout.close()
        selector.close()
    if latest.get('status') == 'running':
        latest['status'] = 'failed'
        latest['stopped_reason'] = 'worker_exited_without_final_report'
    latest['wall_seconds'] = round(time.monotonic() - started, 3)
    add_findings(latest)
    return latest


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, help='与面板中相同的数据根目录，只读')
    parser.add_argument('--pattern', default='bev_det_kof.json', help='当前任务实际读取的 JSON 文件名或通配符')
    parser.add_argument('--keyword', action='append', default=[], help='可重复；匹配逻辑为 OR')
    parser.add_argument('--all-checkpoints', action='store_true', help='普通实验所有checkpoint；基线仍仅最新')
    parser.add_argument('--no-keep-baselines', action='store_true')
    parser.add_argument('--timeout', type=float, default=45, help='父进程时间预算（秒），超时也生成部分报告')
    parser.add_argument('--max-directories', type=int, default=10000)
    parser.add_argument('--max-entries', type=int, default=200000)
    parser.add_argument('--sample-files', type=int, default=6)
    parser.add_argument('--sample-max-mib', type=float, default=8)
    parser.add_argument('--output', default=None, help='报告写到实验目录之外，指定文件须不存在')
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not args.pattern or '/' in args.pattern or '\\' in args.pattern:
        parser.error('--pattern 只接受文件名模式')
    if not 0 < args.timeout <= 300 or not 0 <= args.sample_files <= 30 or not 0 < args.sample_max_mib <= 32:
        parser.error('timeout: (0,300]；sample-files: [0,30]；sample-max-mib: (0,32]')
    if args.max_directories < 1 or args.max_entries < 1:
        parser.error('扫描数量预算必须大于0')
    return args


def main():
    args = arguments()
    if args.worker:
        Probe(args, lambda report: print(json.dumps(report, ensure_ascii=False), flush=True)).run()
        return
    output = args.output or time.strftime('loading-diagnostic-%Y%m%d-%H%M%S.json')
    # Lexical check avoids resolving paths on NFS before the supervisor starts.
    # The output directory must itself be local and must not be a symlink into
    # the data source; resolving arbitrary NFS symlinks here could block.
    data_root = os.path.abspath(os.path.expanduser(args.root))
    report_path = os.path.abspath(os.path.expanduser(output))
    if os.path.commonpath((data_root, report_path)) == data_root:
        raise SystemExit('报告必须保存在数据根目录之外；请用 --output 指定本地磁盘上的文件。')
    output = report_path
    # Reserve before touching NFS; never overwrite an existing report.
    with open(output, 'x', encoding='utf-8') as report_file:
        print('开始只读诊断；不上传数据，不输出路径或业务内容。按 Ctrl+C 可保留部分报告。', flush=True)
        report = supervise(args)
        json.dump(report, report_file, ensure_ascii=False, indent=2)
        report_file.write('\n')
    print('报告已保存：{}（状态：{}）'.format(output, report['status']))


if __name__ == '__main__':
    main()
