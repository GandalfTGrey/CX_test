"""Local SQLite index; the NFS scan runs in a cancellable child process.

No Streamlit imports. A completed generation is published atomically. Interrupted
or incomplete scans never replace the last successful generation.
"""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time
import uuid

from .data import FileEntry, FLAG_NAMES, Issue, Snapshot

FLAT = re.compile(r'(.+)@(\d+)\Z')
ITER = re.compile(r'iter_(\d+)\Z')
BITS = {flag: 1 << i for i, flag in enumerate(FLAG_NAMES)}


class Cancelled(Exception):
    pass


def normalize(root):
    # Do not resolve() or stat NFS in the UI process.
    return os.path.abspath(os.path.expanduser(root))


class Index:
    def __init__(self, root, index_dir=None):
        self.root = normalize(root)
        local = Path(index_dir or os.environ.get('DASHBOARD_INDEX_DIR', '/tmp/leaderboard-index'))
        local.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = str(local / (hashlib.sha256(self.root.encode()).hexdigest() + '.sqlite'))
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS files(
                  batch TEXT, path TEXT, name TEXT, experiment TEXT, iteration INTEGER,
                  mask INTEGER, mtime INTEGER, size INTEGER, PRIMARY KEY(batch,path));
                CREATE INDEX IF NOT EXISTS files_query ON files(batch,name,experiment,iteration);
                CREATE TABLE IF NOT EXISTS issues(batch TEXT, path TEXT, message TEXT);
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def get(db, key, default=None):
        row = db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    @staticmethod
    def put(db, key, value):
        db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', (key, json.dumps(value, ensure_ascii=False)))

    def state(self):
        with self.connect() as db:
            state = self.get(db, 'state', {'status': 'new'})
            state['active'] = self.get(db, 'active', '')
            state['updated'] = self.get(db, 'updated', 0)
        # A worker may have been terminated by the OS or a server restart.
        if state['status'] in ('starting', 'scanning') and time.time() - state.get('heartbeat', 0) > 90:
            state = dict(state, status='stalled', message='扫描进度超过90秒未更新；可取消后重试，旧索引仍保留。')
        return state

    def start(self, force=False):
        state = self.state()
        if not force and (state.get('active') or state['status'] != 'new'):
            return
        # The child holds this same lock throughout the scan, also across sessions.
        with open(self.path + '.lock', 'a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return
            with self.connect() as db:
                current = self.get(db, 'state', {})
                if current.get('status') == 'starting' and time.time() - current.get('heartbeat', 0) < 10:
                    return
                self.put(db, 'cancel', False)
                self.put(db, 'state', {'status': 'starting', 'heartbeat': time.time(), 'started': time.time(),
                                       'directories': 0, 'files': 0, 'entries': 0, 'phase': '启动目录发现'})
        env = dict(os.environ)
        project = str(Path(__file__).resolve().parents[1])
        env['PYTHONPATH'] = project + os.pathsep + env.get('PYTHONPATH', '')
        subprocess.Popen([sys.executable, '-m', 'dashboard.index', '--root', self.root,
                          '--index-dir', str(Path(self.path).parent)], env=env, cwd=project,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)

    def cancel(self):
        with self.connect() as db:
            self.put(db, 'cancel', True)

    def snapshot(self, pattern='*.json', batch=None):
        if not pattern or '/' in pattern or '\\' in pattern:
            raise ValueError('评测模式必须是文件名')
        with self.connect() as db:
            batch = batch or self.get(db, 'active', '')
            rows = db.execute('SELECT * FROM files WHERE batch=? AND name GLOB ? ORDER BY experiment,iteration,path',
                              (batch, pattern)).fetchall()
            problems = db.execute('SELECT path,message FROM issues WHERE batch=? LIMIT 100', (batch,)).fetchall()
        entries = tuple(FileEntry(row['path'], row['experiment'], row['iteration'],
                                  tuple(flag for flag, bit in BITS.items() if row['mask'] & bit),
                                  row['mtime'], row['size']) for row in rows)
        return Snapshot(entries, tuple(Issue(row['path'], row['message']) for row in problems))

    def filenames(self, batch):
        with self.connect() as db:
            return [row[0] for row in db.execute('SELECT DISTINCT name FROM files WHERE batch=? ORDER BY name', (batch,))]

    def metadata(self, entries, batch):
        from .data import _metadata_directories
        paths = {str(directory / name) for entry in entries for directory in _metadata_directories(entry)
                 for name in ('job_config.json', 'param.json')}
        found = {}
        with self.connect() as db:
            for path in paths:
                row = db.execute('SELECT mtime,size FROM files WHERE batch=? AND path=?', (batch, path)).fetchone()
                if row:
                    found[path] = (row['mtime'], row['size'])
        return found


def build(index):
    """Scan all supported layouts once, without per-flag exists() calls or hard
    directory/file-count cutoffs. Symlink targets are followed only to fixed depth.
    """
    with open(index.path + '.lock', 'a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        started = time.time()
        batch = uuid.uuid4().hex
        state = dict(status='scanning', started=started, heartbeat=started, directories=0,
                     entries=0, files=0, errors=0, phase='发现目录', batch=batch)
        last_flush = [0.0]
        pending, issues = [], []

        def flush(force=False):
            if not force and time.monotonic() - last_flush[0] < .25:
                return
            state['heartbeat'] = time.time()
            state['elapsed'] = round(time.time() - started, 2)
            with index.connect() as db:
                if Index.get(db, 'cancel', False):
                    raise Cancelled()
                db.executemany('INSERT OR REPLACE INTO files VALUES (?,?,?,?,?,?,?,?)', pending)
                db.executemany('INSERT INTO issues VALUES (?,?,?)', issues)
                Index.put(db, 'state', state)
            pending.clear()
            issues.clear()
            last_flush[0] = time.monotonic()

        def issue(path, error):
            state['errors'] += 1
            # Bound diagnostic retention, never the scan or useful result count.
            if state['errors'] <= 1000:
                issues.append((batch, path, '{}: {}'.format(type(error).__name__, error)))

        seen = {}

        def children(path):
            # Cache shared base metadata and flat result dirs within one scan.
            if path in seen:
                return seen[path]
            flush()
            state['directories'] += 1
            result = []
            try:
                with os.scandir(path) as stream:
                    for item in stream:
                        result.append(item)
                        state['entries'] += 1
                        flush()
            except OSError as error:
                issue(path, error)
            seen[path] = result
            return result

        def isdir(item):
            try:
                return item.is_dir(follow_symlinks=True)
            except OSError as error:
                issue(item.path, error)
                return False

        def mask(items):
            return sum(BITS[item.name] for item in items if item.name in BITS)

        def collect(items, experiment, iteration, flags):
            for item in items:
                if not item.name.endswith('.json'):
                    continue
                try:
                    if not item.is_file(follow_symlinks=True):
                        continue
                    stat = item.stat(follow_symlinks=True)
                    pending.append((batch, item.path, item.name, experiment, iteration,
                                    flags, stat.st_mtime_ns, stat.st_size))
                    if item.name not in ('job_config.json', 'param.json'):
                        state['files'] += 1
                except OSError as error:
                    issue(item.path, error)
                flush()

        try:
            flush(True)
            root_items = children(index.root)
            directories = [item for item in root_items if isdir(item)]
            # Base dirs first, so flat checkpoints inherit their experiment flags.
            bases = {}
            for item in directories:
                if FLAT.fullmatch(item.name):
                    continue
                items = children(item.path)
                bases[item.name] = mask(items)
                collect(items, item.name, 0, bases[item.name])
                evaluation = next((entry for entry in items if entry.name == 'eval' and isdir(entry)), None)
                if evaluation:
                    for checkpoint in children(evaluation.path):
                        match = ITER.fullmatch(checkpoint.name)
                        if not match or not isdir(checkpoint):
                            continue
                        content = children(checkpoint.path)
                        collect(content, item.name, int(match.group(1)), bases[item.name] | mask(content))
                        seen.pop(checkpoint.path, None)
                    seen.pop(evaluation.path, None)
                seen.pop(item.path, None)
            for item in directories:
                match = FLAT.fullmatch(item.name)
                if match:
                    content = children(item.path)
                    collect(content, match.group(1), int(match.group(2)),
                            bases.get(match.group(1), 0) | mask(content))
                    seen.pop(item.path, None)
            root_results = [item for item in root_items if item.name.endswith('.json')]
            if root_results and not state['files']:
                issue(index.root, ValueError('根目录直接包含结果 JSON，请选择实验目录的上一级；当前目录层级不受支持。'))
            state['phase'] = '提交本地索引'
            flush(True)
            if state['errors']:
                state.update(status='failed', message='发现 {} 个文件系统或布局错误，保留上一完整索引。'.format(state['errors']))
            else:
                state.update(status='complete', phase='索引就绪')
            state['heartbeat'] = time.time()
            with index.connect() as db:
                if Index.get(db, 'cancel', False):
                    raise Cancelled()
                if state['status'] == 'complete':
                    Index.put(db, 'active', batch)
                    Index.put(db, 'updated', time.time())
                Index.put(db, 'state', state)
                # Keep previous generation for in-flight readers; prune older ones.
                recent = [row[0] for row in db.execute('SELECT batch FROM files GROUP BY batch ORDER BY MAX(rowid) DESC LIMIT 3')]
                if recent:
                    marks = ','.join('?' for _ in recent)
                    db.execute('DELETE FROM files WHERE batch NOT IN (' + marks + ')', recent)
                    db.execute('DELETE FROM issues WHERE batch NOT IN (' + marks + ')', recent)
        except Cancelled:
            state.update(status='cancelled', message='扫描已取消；上一完整索引仍可查询。', heartbeat=time.time())
            with index.connect() as db:
                Index.put(db, 'state', state)
                db.execute('DELETE FROM files WHERE batch=?', (batch,))
        except Exception as error:
            state.update(status='failed', message='{}: {}'.format(type(error).__name__, error), heartbeat=time.time())
            with index.connect() as db:
                Index.put(db, 'state', state)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    parser.add_argument('--index-dir', required=True)
    args = parser.parse_args()
    build(Index(args.root, args.index_dir))
