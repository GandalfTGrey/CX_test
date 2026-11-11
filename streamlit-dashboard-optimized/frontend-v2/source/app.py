"""Run: streamlit run app.py. Set DASHBOARD_DATA_ROOT for real evaluations."""
from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

from dashboard.analytics import compare_records, flatten_numbers
from dashboard.context import Context, TAGS
from dashboard.controls import control, reset_page

st.set_page_config(page_title='实验性能追踪', page_icon='🧪', layout='wide')

TASKS = {'god': 'GOD · 占用感知', 'cog': 'COG · 道路认知', 'bev': 'BEV · 障碍物',
         'pnc': 'PNC · 规划', 'pvseg': 'PVSeg · 语义分割', 'depth': 'Depth · 深度',
         'navi': 'Navi · 导航', 'bev-new': 'BEV-new · 障碍物'}
PATTERNS = {
    'god': ['god_eval.json', 'god_obstacle.json', 'god_curb_inroad.json', 'god_raeb_obstacle_mis_detect.json', 'god_raeb_obstacle_distance.json'],
    'cog': ['cog_eval.json'], 'pnc': ['pnc_eval.json'], 'pvseg': ['pvseg_rslt.json'],
    'depth': ['depth_eval.json'], 'navi': ['navi_eval.json'],
    'bev': ['bev_det.json', 'bev_det_kof.json', 'bev_det_rear.json', 'bev_det_rear_kof.json',
            'bev_det_cyclist_distance.json', 'bev_det_cyclist_distance_kof.json',
            'bev_det_rear_cyclist_distance.json', 'bev_det_rear_cyclist_distance_kof.json',
            'bev_det_front_far.json', 'bev_det_front_far_kof.json', 'bev_det_rear_far.json', 'bev_det_rear_far_kof.json',
            'bev_det_front_far_cyclist_distance.json', 'bev_det_front_far_cyclist_distance_kof.json',
            'bev_det_rear_far_cyclist_distance.json', 'bev_det_rear_far_cyclist_distance_kof.json',
            'bev_move.json', 'bev_ghost_probe.json', 'bev_cutin_latency.json', 'result.json'],
    'bev-new': ['bev_det.json', 'bev_det_kof.json', 'bev_det_key_region.json', 'bev_det_rear.json', 'bev_det_rear_kof.json',
                'bev_det_cyclist_distance.json', 'bev_det_cyclist_distance_kof.json',
                'bev_det_rear_cyclist_distance.json', 'bev_det_rear_cyclist_distance_kof.json',
                'bev_det_other.json', 'bev_det_other_kof.json', 'bev_det_other_cyclist_distance.json',
                'bev_det_other_cyclist_distance_kof.json', 'bev_det_lateral.json', 'bev_det_lateral_kof.json',
                'bev_ghost_probe.json', 'bev_cutin_latency_new.json', 'result.json'],
}


def initial_query():
    params = st.query_params
    pin = params.get('pin', 'DEFAULT')
    if pin not in ('DEFAULT', *TAGS.values()):
        pin = 'DEFAULT'
    return {'source': '本地目录',
            'root': os.environ.get('DASHBOARD_DATA_ROOT', ''),
            'keywords': tuple(word.strip() for word in params.get('q', '').split(',') if word.strip())[:100],
            'only_latest': params.get('latest', '1') != '0', 'star_only_latest': True,
            'keep_pinned': True, 'selected_pin_name': pin, 'iter_first': True}


def choose_metric(records, task, namespace):
    paths = sorted({key for record in records for key in flatten_numbers(record.payload)})
    if not paths:
        st.info('这些文件没有可用于数值对比的 JSON 数字。组合字符串仍可在任务指标表和原始结果中查看。')
        return None
    hints = {'pnc': '/follow/FDE_30', 'depth': '/overall/mae(m)', 'pvseg': '/mIoU',
             'navi': '/ade_in_m', 'bev': '/CAR/IoU@0.5 PedDist@1.0/ap/I_BEV/0'}
    index = paths.index(hints[task]) if task in hints and hints[task] in paths else 0
    return control('selectbox', '原始指标路径', paths, index=index, key=f'{namespace}:{task}:metric',
                        help='路径采用 JSON Pointer；数组以索引表示。只对比数值，不推断组合字符串的含义。')


def raw_results(records, task, history, namespace=''):
    with st.expander('检查原始结果与来源'):
        index = control('selectbox', '结果文件', range(len(records)),
                            format_func=lambda number: f'{records[number].entry.experiment} @ {records[number].entry.iteration}',
                            key=f'raw:{namespace}:{task}:{history}')
        record = records[index]
        st.code(record.entry.path, language=None)
        if control('checkbox', '显示所选文件的原始 JSON', key=f'raw-visible:{namespace}:{task}:{history}'):
            st.json(record.payload, expanded=False)


def analysis_view(context, task, history=False):
    if context.index is not None:
        available = [name for name in context.index.filenames(context.batch)
                     if name not in ('param.json', 'job_config.json')]
    else:
        available = sorted({Path(entry.path).name for entry in context.snapshot('*.json').entries
                            if Path(entry.path).name not in ('param.json', 'job_config.json')})
    choices = [name for name in PATTERNS[task] if name in available]
    if not choices:
        st.info('当前目录没有该任务的评测文件。可在下方选择其他 JSON 文件检查数据。')
    with st.expander('其他评测文件', expanded=False):
        custom = control('selectbox', '手动选择文件（按原始 schema 对比）', ['使用任务默认文件'] + available,
                         key=f'file-extra:{context.root}:{task}:{history}', reset_page=True)
    if custom != '使用任务默认文件':
        choices = [custom]
    if not choices:
        return
    filename = control('selectbox', '评测文件', choices,
                       key=f'analysis-file:{context.root}:{task}:{history}', reset_page=True)
    namespace = f'{context.root}:{filename}:{history}'
    baseline = None
    baseline_entry = None
    if not history:
        selectable = context.selection(filename)
        identities = {(entry.experiment, entry.iteration): entry for entry in selectable}
        preference_key = f'baseline:{context.root}:{task}:{filename}'
        saved = st.session_state.get('control_preferences', {}).get(preference_key)
        if saved and saved not in identities:
            matches = [entry for entry in context.snapshot(filename).entries
                       if (entry.experiment, entry.iteration) == saved and 'HIDE' not in entry.flags]
            saved_entry = matches[0] if len(matches) == 1 else None
            if saved_entry:
                identities[saved] = saved_entry
            else:
                st.warning('原基线已不可用；请重新选择基线，不会自动改用其他实验。')
        identifiers = sorted(identities)
        preferred = next((identity for identity in identifiers
                          if set(identities[identity].flags) & set(context.pin_flags)), identifiers[0] if identifiers else None)
        initial = 0 if saved and saved not in identities else (identifiers.index(preferred) + 1 if preferred else 0)
        baseline = control('selectbox', '对比基线', [None] + identifiers, index=initial,
                           format_func=lambda item: '请选择基线' if item is None else f'{item[0]} · {item[1]:,}',
                           key=preference_key, help='基线跨页固定；必要时仅额外读取这个结果。')
        baseline_entry = identities.get(baseline)
    if history:
        from dashboard.data import select_entries
        candidates = select_entries(context.snapshot(filename).entries, keywords=context.keywords,
                                    only_latest=False, baseline_latest=False, keep_pinned=context.keep_pinned)
        experiments = sorted({entry.experiment for entry in candidates})
        chosen = control('multiselect', '对比实验（最多 12 个）', experiments,
                                default=experiments[:min(4, len(experiments))],
                                max_selections=12, key=f'trend-exps:{context.root}:{task}:{filename}', reset_page=True)
        context.history_experiments = tuple(chosen)
        st.caption('先选实验，再读取本页历史 checkpoint。翻页查看其余历史点；缺失指标保留断点，不插造评测点。')
    records = context.records(filename, history=history)
    if not records:
        return
    if not history:
        if baseline_entry is None:
            st.info('选择基线后展示差值对比。')
            return
        reference = next((record for record in records
                          if (record.entry.experiment, record.entry.iteration) == baseline), None)
        if reference is None:
            reference = context.reference(baseline_entry)
        if reference is None:
            st.warning('所选基线读取失败，本次不计算差值，也不会自动替换基线。')
            return
        if reference.entry.path not in {record.entry.path for record in records}:
            records = tuple(records) + (reference,)
            context.extra_references.add(baseline)
        st.caption(f'固定基线：{baseline[0]} @ {baseline[1]:,} · 本页结果与该基线比较')
    metric = choose_metric(records, task, ('trend' if history else 'compare') + ':' + namespace)
    if metric is None:
        raw_results(records, task, history, namespace)
        return
    if history:
        points = [{'实验': record.entry.experiment, 'Checkpoint': record.entry.iteration,
                   '数值': flatten_numbers(record.payload).get(metric)} for record in records
                  if record.entry.experiment in chosen]
        frame = pd.DataFrame(points, columns=['实验', 'Checkpoint', '数值']).sort_values(['实验', 'Checkpoint'])
        missing = int(frame['数值'].isna().sum())
        if missing:
            st.caption(f'{missing} 个 checkpoint 缺少该指标，图中保留断点。')
        if not frame.empty and frame['数值'].notna().any():
            chart = px.line(frame, x='Checkpoint', y='数值', color='实验', markers=True,
                            labels={'数值': metric}, template='plotly_white')
            chart.update_traces(connectgaps=False)
            chart.update_layout(legend=dict(orientation='h', y=-.2), margin=dict(l=10, r=10, t=10, b=10))
            st.plotly_chart(chart, use_container_width=True, key=f'trend-chart:{task}')
        else:
            st.info('所选实验没有该指标的有效数值。')
        st.dataframe(frame, hide_index=True, use_container_width=True)
    else:
        direction = control('selectbox', '指标方向', ['仅显示差值', '越小越好', '越大越好'],
                                    key=f'direction:{context.root}:{task}:{filename}:{metric}')
        frame = pd.DataFrame(compare_records(records, metric, baseline))
        if direction != '仅显示差值':
            sign = -1 if direction == '越小越好' else 1
            def conclusion(row):
                if row['基线']:
                    return '基线'
                delta = row['基线差值']
                if pd.isna(delta):
                    return '缺失'
                return '改善' if delta * sign > 0 else '退化' if delta * sign < 0 else '持平'
            frame['结论'] = frame.apply(conclusion, axis=1)
        st.dataframe(frame, hide_index=True, use_container_width=True, column_config={
            '数值': st.column_config.NumberColumn(format='%.6f'),
            '基线差值': st.column_config.NumberColumn(format='%+.6f'),
            '相对变化 (%)': st.column_config.NumberColumn(format='%+.2f')})
        st.caption('基线差值 = 当前值 − 基线值；相对变化 = 差值 / |基线值| × 100%。基线为 0 或数据缺失时，相对变化留空。')
        visible = frame.dropna(subset=['数值']).copy()
        visible['实验 / Checkpoint'] = visible['实验'] + ' · ' + visible['Checkpoint'].astype(str)
        with st.expander('图形对比与指标说明'):
            show_chart = control('checkbox', '显示本页对比图', key=f'chart:{namespace}:{task}', value=False)
            st.caption('数组按原始下标对齐，请确保 schema、距离定义和单位一致。图最多展示本页前25个有效结果，完整数值见上表。')
        if show_chart and not visible.empty:
            chart = px.bar(visible.head(25), x='数值', y='实验 / Checkpoint', color='基线', orientation='h',
                           color_discrete_map={True: '#0d9488', False: '#64748b'}, template='plotly_white')
            chart.update_layout(showlegend=False, yaxis=dict(autorange='reversed'),
                                height=min(500, max(260, 22 * min(25, len(visible)))), margin=dict(t=10, b=10, l=10, r=10))
            st.plotly_chart(chart, use_container_width=True, key=f'compare-chart:{task}')
    csv = frame.to_csv(index=False).encode('utf-8-sig')
    st.download_button('下载本页对比 CSV', csv, f'{task}-{"trend" if history else "compare"}-page-{context.page+1}.csv', 'text/csv', on_click=None)
    raw_results(records, task, history, namespace)


@st.cache_data(ttl=60, max_entries=32, show_spinner=False)
def training_points(path, interval, refresh_token):
    from dashboard.training import read_training_logs
    return read_training_logs(path, interval)


def training_view(context):
    st.caption('只读训练日志；支持本地文件、Rank-0 目录、训练输出目录和 OBS 路径。OBS 需要运行环境已配置 moxing。')
    with st.form('training-query'):
        text = st.text_area('训练日志，每行「名称 | 路径」', placeholder='baseline | /path/to/log\nnew-run | obs://bucket/output', height=110)
        interval = st.number_input('最小迭代采样间隔', min_value=1, value=100, step=100)
        submitted = st.form_submit_button('读取并对比', type='primary')
    if submitted:
        st.session_state['training-query-applied'] = (text, int(interval))
    applied = st.session_state.get('training-query-applied')
    if not applied:
        return
    points, summaries = [], []
    from dashboard.training import speed_summary
    lines = [line.strip() for line in applied[0].splitlines() if line.strip() and not line.lstrip().startswith('#')]
    if len(lines) > 8:
        st.error('每次最多对比 8 份日志，请缩小范围。')
        return
    for line in lines:
        name, separator, path = line.partition('|')
        if not separator or not name.strip() or not path.strip():
            st.warning(f'格式无效：{line}。请使用「名称 | 路径」。')
            continue
        try:
            samples = training_points(path.strip(), applied[1], context.refresh_token)
            summaries.append({'实验': name.strip(), '中位耗时 (秒/iter)': speed_summary(samples), '有效区间': len(samples)})
            points.extend({'实验': name.strip(), 'Checkpoint': iteration, '秒/iter': value} for iteration, value in samples)
        except (OSError, ValueError, RuntimeError) as error:
            context.issue(f'{path.strip()}: {error}')
    if points:
        st.plotly_chart(px.line(pd.DataFrame(points), x='Checkpoint', y='秒/iter', color='实验', markers=True), use_container_width=True)
    else:
        st.info('没有有效的训练速度样本。')
    st.dataframe(pd.DataFrame(summaries), hide_index=True, use_container_width=True)


def main():
    from dashboard.workbench import run
    run(initial_query, analysis_view, training_view, TASKS)


if __name__ == '__main__':
    main()
