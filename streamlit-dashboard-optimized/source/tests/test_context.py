from pathlib import Path
from unittest.mock import patch

from dashboard.context import Context
from dashboard.data import FileEntry, Issue, Record, Snapshot


def entry(path, name='exp', iteration=1):
    return FileEntry(str(path), name, iteration, (), 1, 1)


def test_query_filters_before_loading_json():
    entries = tuple(entry(f'/data/{name}@{iteration}/pnc_eval.json', name, iteration)
                    for name in ['matched', 'other'] for iteration in [1, 2])
    calls = []
    def load(selected, reader):
        calls.extend(selected)
        return tuple(Record(item, {}, {}) for item in selected), ()
    with patch('dashboard.context.cached_scan', return_value=(Snapshot(entries, ()), 1)), patch('dashboard.context.load_records', side_effect=load):
        result = Context('/data', 'pnc', keywords=('matched',)).records('pnc_eval.json')
    assert len(result) == 1
    assert calls[0].experiment == 'matched'
    assert calls[0].iteration == 2


def test_duplicate_layout_is_excluded_in_all_views():
    entries = (entry('/data/exp@1/pnc_eval.json'), entry('/data/exp/eval/iter_1/pnc_eval.json'))
    with patch('dashboard.context.cached_scan', return_value=(Snapshot(entries, ()), 1)), patch('dashboard.context.load_records', return_value=((), ())) as load:
        context = Context('/data', 'pnc')
        assert not context.records('pnc_eval.json')
        assert not load.call_args.args[0]
        assert any('重复结果' in message for message in context.messages)


def test_broken_latest_never_silently_falls_back():
    entries = (entry('/data/exp@1/pnc_eval.json'), entry('/data/exp@2/pnc_eval.json', iteration=2))
    with patch('dashboard.context.cached_scan', return_value=(Snapshot(entries, ()), 1)), patch('dashboard.context.load_records', return_value=((), (Issue(entries[1].path, 'invalid JSON'),))) as load:
        context = Context('/data', 'pnc')
        assert not context.records('pnc_eval.json')
        assert [item.iteration for item in load.call_args.args[0]] == [2]
        assert context.messages
