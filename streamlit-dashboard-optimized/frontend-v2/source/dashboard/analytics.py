"""Numerical comparisons use raw scalar values, never formatted cell strings."""
from __future__ import annotations

import math
from collections.abc import Mapping


def flatten_numbers(payload, *, limit=10000, max_depth=24):
    """Return finite JSON numbers under unambiguous JSON Pointer paths.

    Strings such as precision '12/30' are deliberately excluded. Lists retain
    their index; their labels require the task-specific schema, not inference.
    """
    result = {}

    def visit(value, path, depth):
        if depth > max_depth or len(result) >= limit:
            return
        if isinstance(value, bool):
            return
        if isinstance(value, (float, int)):
            try:
                numeric = float(value)
            except (ValueError, OverflowError):
                return
            if math.isfinite(numeric):
                result[path or '/'] = numeric
        elif isinstance(value, Mapping):
            for key, child in value.items():
                escaped = str(key).replace('~', '~0').replace('/', '~1')
                visit(child, f'{path}/{escaped}', depth + 1)
                if len(result) >= limit:
                    break
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, f'{path}/{index}', depth + 1)
                if len(result) >= limit:
                    break

    visit(payload, '', 0)
    return result


def compare_records(records, metric, baseline):
    """baseline is (experiment, iteration); no missing value becomes zero."""
    flattened = [(record, flatten_numbers(record.payload)) for record in records]
    reference = next((values.get(metric) for record, values in flattened
                      if (record.entry.experiment, record.entry.iteration) == baseline), None)
    result = []
    for record, values in flattened:
        value = values.get(metric)
        delta = value - reference if value is not None and reference is not None else None
        relative = delta / abs(reference) * 100 if delta is not None and reference != 0 else None
        result.append({'实验': record.entry.experiment, 'Checkpoint': record.entry.iteration,
                       '数值': value, '基线差值': delta, '相对变化 (%)': relative,
                       '基线': (record.entry.experiment, record.entry.iteration) == baseline})
    return result
