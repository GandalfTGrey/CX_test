"""Per-rerun query context; no cross-session mutable business state."""
from __future__ import annotations

from dataclasses import dataclass, field
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd
import streamlit as st
import time
from st_aggrid import GridOptionsBuilder, GridUpdateMode
from st_aggrid.shared import JsCode

from .cache import cached_payload, cached_scan
from .data import load_records, select_entries
from .compat import safe_aggrid
from .loading import get_job

TAGS = {'STAR': '🌟', 'SUN': '🌞', 'GALAXY': '🌌', 'FLAG': '🚩',
        'APPLE': '🍎', 'GRAPE': '🍇', 'SAKURA': '🌸', 'STAR2': '📌'}


class ResultsPending(Exception):
    """The workbench renders one loading state before any first-result empty UI."""


def safe_url(value):
    if not isinstance(value, str):
        return None
    parsed = urlparse(value)
    return value if parsed.scheme in ('https', 'http') and parsed.netloc else None


@dataclass
class Context:
    root: str
    task: str
    keywords: tuple = ()
    only_latest: bool = True
    star_only_latest: bool = True
    iter_first: bool = True
    selected_pin_name: str = 'DEFAULT'
    keep_pinned: bool = True
    refresh_token: str = ''
    max_files: int = 2000
    index: object = None
    batch: str = ''
    page: int = 0
    page_size: int = 50
    history_experiments: tuple = ()
    asynchronous: bool = False
    session: str = ''
    view: str = '任务指标'
    selections: dict = field(default_factory=dict)
    displayed: list = field(default_factory=list)
    extra_references: set = field(default_factory=set)
    requested: bool = False
    previous: dict = field(default_factory=dict)
    pending: list = field(default_factory=list)
    totals: dict = field(default_factory=dict)
    failed: bool = False
    preserved: bool = False
    messages: list = field(default_factory=list)
    snapshots: dict = field(default_factory=dict)
    loaded: dict = field(default_factory=dict)
    table_number: int = 0

    @property
    def pin_flags(self):
        if self.selected_pin_name == 'DEFAULT':
            return ('STAR', 'SUN')
        return tuple(key for key, value in TAGS.items() if value == self.selected_pin_name)

    def issue(self, message):
        message = str(message)
        if message not in self.messages:
            self.messages.append(message)

    def snapshot(self, pattern):
        if pattern not in self.snapshots:
            if self.index is not None:
                self.snapshots[pattern] = (self.index.snapshot(pattern, self.batch), self.index.state()['updated'])
            else:
                self.snapshots[pattern] = cached_scan(self.root, pattern, self.refresh_token)
            for issue in self.snapshots[pattern][0].issues:
                self.issue(f'{issue.path}: {issue.message}')
        return self.snapshots[pattern][0]

    def selection(self, pattern, *, history=False):
        key = (pattern, history)
        if key not in self.selections:
            snapshot = self.snapshot(pattern)
            pinned = {entry.experiment for entry in snapshot.entries if set(entry.flags) & set(self.pin_flags)}
            entries = select_entries(snapshot.entries, keywords=self.keywords,
                                     only_latest=False if history else self.only_latest,
                                     baseline_latest=False if history else self.star_only_latest,
                                     pinned_experiments=pinned, keep_pinned=self.keep_pinned)
            if history:
                entries = tuple(entry for entry in entries if entry.experiment in self.history_experiments)
            identities = Counter((entry.experiment, entry.iteration, Path(entry.path).name) for entry in entries)
            collisions = {identity for identity, count in identities.items() if count > 1}
            for experiment, iteration, filename in sorted(collisions):
                self.issue(f'{experiment}@{iteration}/{filename}: 两种目录布局中出现重复结果，已排除冲突文件。')
            entries = tuple(entry for entry in entries
                            if (entry.experiment, entry.iteration, Path(entry.path).name) not in collisions)
            self.selections[key] = entries
        return self.selections[key]

    def descriptor(self, pattern, history=False):
        return dict(root=self.root, task=self.task, view=self.view, pattern=pattern,
                    keywords=self.keywords, only_latest=self.only_latest,
                    keep_pinned=self.keep_pinned, page=self.page, page_size=self.page_size,
                    history=history, experiments=self.history_experiments)

    def _resolve(self, entries, key, description, *, reference=False):
        if self.asynchronous and self.index is not None:
            metadata = self.index.metadata(entries, self.batch)
            signature = (self.session, self.root, key, self.refresh_token,
                         tuple((entry.path, entry.mtime_ns, entry.size) for entry in entries),
                         tuple(sorted(metadata.items())))
            job = get_job(signature, entries, metadata)
            previous_key = (self.root, self.task, self.view, key)
            previous = self.previous.get(previous_key)
            if not job.done or job.cancelled.is_set() or (entries and not job.records and job.issues):
                if not job.done:
                    self.pending.append(job)
                else:
                    self.failed = True
                self.preserved = self.preserved or previous is not None
                for issue in job.issues:
                    self.issue(f'{issue.path}: {issue.message}')
                if previous:
                    if not reference and len(previous) > 3:
                        self.displayed.append((previous[3], previous[1]))
                    return previous[0]
                if not job.done:
                    raise ResultsPending()
                return ()
            for issue in job.issues:
                self.issue(f'{issue.path}: {issue.message}')
            if previous is None or previous[2] != signature or (len(previous) > 3 and previous[3] != description):
                timestamp = previous[1] if previous and previous[2] == signature else time.time()
                previous = (job.records, timestamp, signature, description)
                self.previous[previous_key] = previous
            if not reference:
                self.displayed.append((previous[3], previous[1]))
            return job.records
        acceptable, total = [], 0
        for entry in entries:
            if entry.size > 16 * 1024 * 1024 or total + entry.size > 32 * 1024 * 1024:
                self.issue(f'{entry.path}: 文件超过本页读取预算，已单独跳过。')
                continue
            acceptable.append(entry)
            total += entry.size
        def reader(path, mtime_ns, size):
            return cached_payload(path, mtime_ns, size, self.refresh_token)
        records, issues = load_records(acceptable, reader=reader)
        for issue in issues:
            self.issue(f'{issue.path}: {issue.message}')
        return records

    def records(self, pattern, *, history=False):
        key = (pattern, history)
        self.requested = True
        if key not in self.loaded:
            entries = self.selection(pattern, history=history)
            pinned = {entry.experiment for entry in entries if set(entry.flags) & set(self.pin_flags)}
            order = sorted({(entry.experiment, entry.iteration) for entry in entries},
                           key=lambda pair: (pair[0] not in pinned, pair[0], -pair[1]))
            self.totals[key] = len(order)
            pages = max(1, (len(order) + self.page_size - 1) // self.page_size)
            self.page = max(0, min(self.page, pages - 1))
            page_keys = set(order[self.page * self.page_size:(self.page + 1) * self.page_size])
            normal_keys = set(page_keys)
            if not history:
                page_keys.update([item for item in order if item[0] in pinned][:8])
            self.extra_references.update(page_keys - normal_keys)
            selected = tuple(entry for entry in entries if (entry.experiment, entry.iteration) in page_keys)
            self.loaded[key] = self._resolve(selected, key, self.descriptor(pattern, history))
        return self.loaded[key]

    def reference(self, entry):
        key = ('reference', entry.path)
        if key not in self.loaded:
            self.loaded[key] = self._resolve((entry,), key, self.descriptor(Path(entry.path).name), reference=True)
        return next(iter(self.loaded[key]), None)

    def read_results(self, pattern, **kwargs):
        records = self.records(pattern)
        values, metadata = {}, {}
        group = next((kind for kind in ('pv', 'traffic', 'flash', 'attr')
                      if kwargs.get(f'is_{kind}')), None)
        for record in records:
            entry = record.entry
            key = (entry.experiment, entry.iteration)
            payload = record.payload
            if group:
                from pathlib import Path
                filename = Path(entry.path).name
                if group == 'pv':
                    name = filename[7:] if filename.startswith('simple_') else filename
                    name = name[:-5] if name.endswith('.json') else name
                elif group == 'attr':
                    name = (filename[:-5] if filename.endswith('.json') else filename)[5:]
                elif group == 'traffic':
                    parts = filename.split('_')
                    name = (parts[1], ''.join(parts[2:-1]))
                else:
                    name = filename.split('_')[1]
                values.setdefault(key, {})[name] = payload
            else:
                if key in values:
                    self.issue(f'{entry.experiment}@{entry.iteration}: 多个文件对应同一任务结果，已拒绝覆盖。')
                    continue
                values[key] = payload
            metadata[entry.experiment] = safe_url(record.metadata.get('romaDetailUrl'))
            flags = [flag for flag in TAGS if flag in entry.flags]
            prioritized = [flag for flag in self.pin_flags if flag in flags]
            if flags:
                metadata[f'{entry.experiment}-STARSTAR'] = (prioritized or flags)[0]
        return values, metadata

    def prepare_table(self, lines, header, head_width=350):
        if not header:
            header = ['Exp']
        regular, pinned = [], []
        for row in sorted(lines, key=lambda row: (row[1], row[2]), reverse=True):
            # Legacy row: time, experiment, numeric iteration, cells..., flag.
            if len(row) != len(header) + 4:
                self.issue(f'{row[1] if len(row) > 1 else "未知实验"}: 列数不一致，该行未展示，避免指标错列。')
                continue
            cells, flag = list(row[3:-1]), row[-1]
            if flag in TAGS:
                cells[0] = f'{TAGS[flag]}{cells[0]}'
            mapped = dict(zip(header, cells))
            if 'roma' in mapped:
                mapped['roma'] = safe_url(mapped['roma'])
            (pinned if flag in self.pin_flags else regular).append(mapped)
        data = pd.DataFrame(regular, columns=header)
        builder = GridOptionsBuilder.from_dataframe(data)
        builder.configure_default_column(resizable=True, filterable=True, sortable=True,
                                         editable=False, minWidth=95)
        builder.configure_column('Exp', header_name='iteration@ExpName' if self.iter_first else 'ExpName@iteration',
                                 width=min(head_width, 300), minWidth=180, pinned='left')
        comparator = JsCode('''function(a, b) {
          function leading(value) {
            if (typeof value === 'number' && Number.isFinite(value)) return value;
            if (typeof value !== 'string') return null;
            const match = value.trim().match(/^[+-]?(?:\\d+\\.?\\d*|\\.\\d+)(?:[eE][+-]?\\d+)?(?=\\s*(?:$|[(/,%]))/);
            return match && Number.isFinite(Number(match[0])) ? Number(match[0]) : null;
          }
          const x = leading(a), y = leading(b);
          if (x !== null && y !== null) return x - y;
          return String(a ?? '').localeCompare(String(b ?? ''), undefined, {numeric: true});
        }''')
        for column in header[1:]:
            if column != 'roma':
                builder.configure_column(column, comparator=comparator,
                                         headerTooltip='复合指标按首个显示数值排序；精确单指标对比请使用基线对比。')
        builder.configure_grid_options(animateRows=False, ensureDomOrder=True,
                                       suppressMovableColumns=False, rowHeight=34,
                                       pagination=False, suppressPaginationPanel=True,
                                       headerHeight=42, suppressCellFocus=False)
        return data, pinned, builder

    def grid(self, data, **kwargs):
        # Browser sort/filter stays local; no MODEL_CHANGED round trip per click.
        kwargs.pop('update_mode', None)
        kwargs['update_mode'] = GridUpdateMode.NO_UPDATE
        kwargs['update_on'] = []
        kwargs['height'] = max(220, min(int(kwargs.get('height', 550)), 760))
        kwargs['key'] = f'grid:{self.task}:{self.root}:{self.table_number}'
        kwargs['theme'] = 'streamlit'
        options = kwargs.setdefault('gridOptions', {})
        options['pagination'] = False
        options['suppressPaginationPanel'] = True
        options['suppressRowClickSelection'] = True
        options['rowSelection'] = 'single'
        options['onGridSizeChanged'] = JsCode('''function(p) {
          const width = document.querySelector('.ag-root-wrapper')?.clientWidth || window.innerWidth;
          const api = p.api.applyColumnState ? p.api : p.columnApi;
          const col = p.api.getColumn ? p.api.getColumn('Exp') : p.columnApi?.getColumn('Exp');
          const desired = width < 640 ? null : 'left';
          if (api && col && col.getPinned() !== desired) {
            api.applyColumnState({state: [{colId: 'Exp', pinned: desired}]});
          }
        }''')
        # Task tables are read-only; avoid legacy selection checkboxes.
        for definition in options.get('columnDefs', []):
            definition.pop('checkboxSelection', None)
            definition.pop('headerCheckboxSelection', None)
        rows = len(data) + len(options.get('pinnedTopRowData', []))
        kwargs['height'] = min(620, max(180, 56 + rows * 34))
        self.table_number += 1
        result = safe_aggrid(data, **kwargs)
        pinned = kwargs.get('gridOptions', {}).get('pinnedTopRowData', [])
        exported = pd.concat([pd.DataFrame(pinned), data], ignore_index=True)
        st.download_button('下载当前指标表 CSV', exported.to_csv(index=False).encode('utf-8-sig'),
                           f'{self.task}-metrics.csv', 'text/csv', key=f'csv:{self.task}:{self.table_number}', on_click=None)
        st.caption('CSV 导出当前服务端分页的结果（含本页置顶行）；表内排序和临时过滤作用于本页。')
        return result
