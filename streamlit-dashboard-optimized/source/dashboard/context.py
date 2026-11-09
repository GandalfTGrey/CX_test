"""Per-rerun query context; no cross-session mutable business state."""
from __future__ import annotations

from dataclasses import dataclass, field
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd
import streamlit as st
from st_aggrid import GridOptionsBuilder, GridUpdateMode
from st_aggrid.shared import JsCode

from .cache import cached_payload, cached_scan
from .data import load_records, select_entries
from .compat import safe_aggrid

TAGS = {'STAR': '🌟', 'SUN': '🌞', 'GALAXY': '🌌', 'FLAG': '🚩',
        'APPLE': '🍎', 'GRAPE': '🍇', 'SAKURA': '🌸', 'STAR2': '📌'}


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
            self.snapshots[pattern] = cached_scan(self.root, pattern, self.refresh_token)
            for issue in self.snapshots[pattern][0].issues:
                self.issue(f'{issue.path}: {issue.message}')
        return self.snapshots[pattern][0]

    def records(self, pattern, *, history=False):
        key = (pattern, history)
        if key not in self.loaded:
            snapshot = self.snapshot(pattern)
            pinned = tuple({entry.experiment for entry in snapshot.entries
                            if set(entry.flags) & set(self.pin_flags)})
            entries = select_entries(snapshot.entries, keywords=self.keywords,
                                     only_latest=False if history else self.only_latest,
                                     baseline_latest=False if history else self.star_only_latest,
                                     pinned_experiments=pinned, keep_pinned=self.keep_pinned)
            identities = Counter((entry.experiment, entry.iteration, Path(entry.path).name) for entry in entries)
            collisions = {identity for identity, count in identities.items() if count > 1}
            if collisions:
                for experiment, iteration, filename in sorted(collisions):
                    self.issue(f'{experiment}@{iteration}/{filename}: 两种目录布局中出现重复结果，已排除冲突文件，请保留唯一来源。')
                entries = tuple(entry for entry in entries
                                if (entry.experiment, entry.iteration, Path(entry.path).name) not in collisions)
            if len(entries) > self.max_files:
                self.issue(f'匹配 {len(entries)} 个文件，超过单次 {self.max_files} 个的读取上限。请缩小关键词或启用仅最新 checkpoint。')
                self.loaded[key] = ()
                return ()
            total_bytes = sum(entry.size for entry in entries)
            if total_bytes > 128 * 1024 * 1024 or any(entry.size > 16 * 1024 * 1024 for entry in entries):
                self.issue('本次匹配数据超过读取预算（单文件 16 MiB / 单次 128 MiB），请缩小筛选或预聚合评测数据。')
                self.loaded[key] = ()
                return ()
            def reader(path, mtime_ns, size):
                return cached_payload(path, mtime_ns, size, self.refresh_token)
            records, issues = load_records(entries, reader=reader)
            for issue in issues:
                self.issue(f'{issue.path}: {issue.message}')
            self.loaded[key] = records
        return self.loaded[key]

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
                                 width=head_width, pinned='left')
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
                                       pagination=True, paginationPageSize=50,
                                       paginationPageSizeSelector=[25, 50, 100])
        return data, pinned, builder

    def grid(self, data, **kwargs):
        # Browser sort/filter stays local; no MODEL_CHANGED round trip per click.
        kwargs.pop('update_mode', None)
        kwargs['update_mode'] = GridUpdateMode.NO_UPDATE
        kwargs['update_on'] = []
        kwargs['height'] = max(220, min(int(kwargs.get('height', 550)), 760))
        kwargs['key'] = f'grid:{self.task}:{self.root}:{self.table_number}'
        kwargs['theme'] = 'streamlit'
        self.table_number += 1
        result = safe_aggrid(data, **kwargs)
        pinned = kwargs.get('gridOptions', {}).get('pinnedTopRowData', [])
        exported = pd.concat([pd.DataFrame(pinned), data], ignore_index=True)
        st.download_button('下载当前指标表 CSV', exported.to_csv(index=False).encode('utf-8-sig'),
                           f'{self.task}-metrics.csv', 'text/csv', key=f'csv:{self.task}:{self.table_number}', on_click=None)
        st.caption('复合指标列按首个显示数值排序。CSV 包含当前数据筛选与置顶行；表内临时过滤只作用于浏览器。')
        return result
