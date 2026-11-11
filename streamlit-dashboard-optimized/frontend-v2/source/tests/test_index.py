"""Scale, consistency and filesystem compatibility of the local index."""
import json
from pathlib import Path
import time
from unittest.mock import patch

import pytest

from dashboard.context import Context, ResultsPending
from dashboard.index import Index, build
from dashboard.loading import Job


def result(root, name, content='{"score":1}'):
    path = root / name / 'pnc_eval.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def test_more_than_10000_directories_and_128_mib_are_queryable_by_page(tmp_path):
    root = tmp_path / 'source'
    root.mkdir()
    for number in range(10010):
        (root / ('run{:05d}@20'.format(number))).mkdir()
    # Real files, real directory scan, valid JSON padding; no fabricated stats.
    for number in range(3001):
        result(root, 'run{:05d}@20'.format(number), '{"score":1}' + ' ' * 64989)
    index = Index(str(root), str(tmp_path / 'local'))
    with patch('os.path.exists', side_effect=AssertionError('per-flag exists must not run')):
        build(index)
    state = index.state()
    assert state['status'] == 'complete'
    assert state['directories'] > 10000
    entries = index.snapshot('pnc_eval.json').entries
    assert len(entries) == 3001
    assert sum(entry.size for entry in entries) > 128 * 1024 * 1024
    context = Context(str(root), 'pnc', index=index, batch=state['active'], page_size=50)
    selected = context.records('pnc_eval.json')
    assert len(selected) == 50
    assert context.totals[('pnc_eval.json', False)] == 3001
    assert {record.entry.experiment for record in selected} == {'run{:05d}'.format(n) for n in range(50)}
    assert not context.messages


def test_index_queries_do_not_access_source_or_rescan(tmp_path):
    root = tmp_path / 'source'
    result(root, 'exp@10')
    index = Index(str(root), str(tmp_path / 'local'))
    build(index)
    with patch('os.scandir', side_effect=AssertionError('no source scan during queries')):
        assert len(index.snapshot('pnc_eval.json').entries) == 1
        assert len(index.snapshot('*.json').entries) == 1


def test_failed_or_cancelled_scan_keeps_last_generation(tmp_path):
    root = tmp_path / 'source'
    result(root, 'old@10')
    index = Index(str(root), str(tmp_path / 'local'))
    build(index)
    active = index.state()['active']
    with patch('os.scandir', side_effect=PermissionError('synthetic inaccessible mount')):
        build(index)
    assert index.state()['status'] == 'failed'
    assert index.state()['active'] == active
    assert len(index.snapshot('pnc_eval.json').entries) == 1
    index.cancel()
    build(index)
    assert index.state()['status'] == 'cancelled'
    assert index.state()['active'] == active


def test_symlink_layout_and_base_flags_and_metadata(tmp_path):
    root = tmp_path / 'source'
    target = tmp_path / 'target'
    result(target, 'source@20')
    root.mkdir()
    (root / 'exp@20').symlink_to(target / 'source@20', target_is_directory=True)
    (root / 'exp').mkdir()
    (root / 'exp' / 'STAR').touch()
    (root / 'exp' / 'param.json').write_text('{"romaDetailUrl":"https://example.com/job"}')
    index = Index(str(root), str(tmp_path / 'local'))
    build(index)
    entries = index.snapshot('pnc_eval.json').entries
    assert len(entries) == 1 and 'STAR' in entries[0].flags
    metadata = index.metadata(entries, index.state()['active'])
    job = Job(entries, metadata)
    for _ in range(200):
        if job.done:
            break
        time.sleep(.01)
    assert job.records[0].metadata['romaDetailUrl'] == 'https://example.com/job'


def test_large_and_bad_files_never_discard_valid_peer(tmp_path):
    root = tmp_path / 'source'
    result(root, 'a-valid@1')
    result(root, 'b-bad@1', '{invalid')
    large = result(root, 'c-large@1', '')
    with large.open('r+b') as stream:
        stream.truncate(17 * 1024 * 1024)
    index = Index(str(root), str(tmp_path / 'local'))
    build(index)
    entries = index.snapshot('pnc_eval.json').entries
    job = Job(entries, {})
    for _ in range(200):
        if job.done:
            break
        time.sleep(.01)
    assert len(job.records) == 1
    assert job.records[0].entry.experiment == 'a-valid'
    assert len(job.issues) == 2


def test_async_failed_refresh_preserves_last_success(tmp_path):
    root = tmp_path / 'source'
    path = result(root, 'good@10')
    index = Index(str(root), str(tmp_path / 'local'))
    build(index)
    previous = {}
    def settle(nonce):
        for _ in range(200):
            context = Context(str(root), 'pnc', index=index, batch=index.state()['active'],
                              asynchronous=True, session='synthetic', previous=previous, refresh_token=nonce)
            try:
                records = context.records('pnc_eval.json')
            except ResultsPending:
                time.sleep(.01)
                continue
            if not context.pending:
                return context, records
            time.sleep(.01)
        raise AssertionError('job did not finish')
    good, records = settle('initial')
    assert len(records) == 1 and not good.failed
    path.write_text('{broken-json-now')
    build(index)
    failed, retained = settle('retry')
    assert failed.failed and failed.preserved
    assert retained == records


def test_subprocess_scan_completes(tmp_path):
    root = tmp_path / 'source'
    result(root, 'exp@10')
    index = Index(str(root), str(tmp_path / 'local'))
    index.start()
    for _ in range(200):
        state = index.state()
        if state['status'] == 'complete':
            break
        time.sleep(.02)
    assert state['status'] == 'complete'
