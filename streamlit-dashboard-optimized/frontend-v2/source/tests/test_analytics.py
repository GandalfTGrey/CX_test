from types import SimpleNamespace

from dashboard.analytics import compare_records, flatten_numbers


def record(name, iteration, value):
    return SimpleNamespace(entry=SimpleNamespace(experiment=name, iteration=iteration), payload=value)


def test_scalar_paths_do_not_guess_ratios_or_boolean_metrics():
    assert flatten_numbers({'a/b': {'~x': [1, '3/4', True, float('nan')]}}) == {'/a~1b/~0x/0': 1.0}


def test_missing_and_zero_baselines():
    records = [record('base', 10, {'loss': 0}), record('run', 20, {'loss': 2}), record('missing', 20, {})]
    rows = compare_records(records, '/loss', ('base', 10))
    assert rows[1]['基线差值'] == 2
    assert rows[1]['相对变化 (%)'] is None
    assert rows[2]['数值'] is None
    assert rows[2]['基线差值'] is None


def test_negative_baseline_uses_absolute_denominator_and_numeric_sort():
    rows = compare_records([record('a', 1, {'x': -2}), record('b', 1, {'x': -1})], '/x', ('a', 1))
    assert rows[1]['相对变化 (%)'] == 50


def test_bounded_flatten():
    assert len(flatten_numbers(list(range(100)), limit=5)) == 5
