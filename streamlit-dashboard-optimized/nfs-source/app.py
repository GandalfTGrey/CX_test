"""Run: streamlit run app.py. Set DASHBOARD_DATA_ROOT for real evaluations."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import time
import uuid

import pandas as pd
import plotly.express as px
import streamlit as st

from dashboard.analytics import compare_records, flatten_numbers
from dashboard.context import Context, TAGS

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
DEMO_ROOT = str(Path(__file__).parent / 'demo_data')


def initial_query():
    params = st.query_params
    pin = params.get('pin', 'DEFAULT')
    if pin not in ('DEFAULT', *TAGS.values()):
        pin = 'DEFAULT'
    return {'source': '本地目录' if os.environ.get('DASHBOARD_DATA_ROOT') else '演示数据',
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
    return st.selectbox('原始指标路径', paths, index=index, key=f'{namespace}:{task}:metric',
                        help='路径采用 JSON Pointer；数组以索引表示。只对比数值，不推断组合字符串的含义。')


def raw_results(records, task, history):
    with st.expander('检查原始结果与来源'):
        index = st.selectbox('结果文件', range(len(records)),
                            format_func=lambda number: f'{records[number].entry.experiment} @ {records[number].entry.iteration}',
                            key=f'raw:{task}:{history}')
        record = records[index]
        st.code(record.entry.path, language=None)
        if st.checkbox('显示所选文件的原始 JSON', key=f'raw-visible:{task}:{history}'):
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
        custom = st.selectbox('手动选择文件（按原始 schema 对比）', ['使用任务默认文件'] + available,
                             key=f'file-extra:{task}:{history}')
    if custom != '使用任务默认文件':
        choices = [custom]
    if not choices:
        return
    filename = st.selectbox('评测文件', choices, key=f'analysis-file:{task}:{history}')
    if history:
        st.caption('趋势读取匹配实验的全部 checkpoint，独立于「仅最新」开关。相邻点用直线连接，不插值生成评测值。')
        from dashboard.data import select_entries
        candidates = select_entries(context.snapshot(filename).entries, keywords=context.keywords,
                                    only_latest=False, baseline_latest=False, keep_pinned=context.keep_pinned)
        experiments = sorted({entry.experiment for entry in candidates})
        chosen = st.multiselect('对比实验（最多 12 个）', experiments,
                                default=experiments[:min(4, len(experiments))],
                                max_selections=12, key=f'trend-exps:{task}',
                                on_change=lambda: st.session_state.update(result_page=0))
        context.history_experiments = tuple(chosen)
        st.caption('先按实验筛选索引，再读取当前页 checkpoint；翻页可查看其余历史点。')
    with st.spinner('读取所选评测结果…'):
        records = context.records(filename, history=history)
    if not records:
        st.info('当前筛选下没有可读结果。请查看页面底部的数据诊断。')
        return
    metric = choose_metric(records, task, 'trend' if history else 'compare')
    if metric is None:
        raw_results(records, task, history)
        return
    st.caption('数组指标按原始下标对齐，请确认各实验使用相同 schema、距离配置与单位。专用表格按任务定义显示类别和距离。')
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
        identifiers = [(record.entry.experiment, record.entry.iteration) for record in records]
        first = next((index for index, record in enumerate(records) if set(record.entry.flags) & {'STAR', 'SUN'}), 0)
        left, right = st.columns([2, 1])
        baseline = left.selectbox('对比基线', identifiers, index=first,
                                  format_func=lambda item: f'{item[0]} · {item[1]:,}', key=f'baseline:{task}')
        direction = right.selectbox('指标方向', ['仅显示差值', '越小越好', '越大越好'],
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
        if len(visible) <= 80 and not visible.empty:
            chart = px.bar(visible, x='数值', y='实验 / Checkpoint', color='基线', orientation='h',
                           color_discrete_map={True: '#0d9488', False: '#64748b'}, template='plotly_white')
            chart.update_layout(showlegend=False, yaxis=dict(autorange='reversed'),
                                height=min(850, max(260, 32 * len(visible))), margin=dict(t=10, b=10, l=10, r=10))
            st.plotly_chart(chart, use_container_width=True, key=f'compare-chart:{task}')
    csv = frame.to_csv(index=False).encode('utf-8-sig')
    st.download_button('下载当前对比 CSV', csv, f'{task}-{"trend" if history else "compare"}.csv', 'text/csv', on_click=None)
    raw_results(records, task, history)


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
    run(initial_query, analysis_view, training_view, TASKS, DEMO_ROOT)


if __name__ == '__main__':
    main()
