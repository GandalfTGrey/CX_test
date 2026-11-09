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
    all_entries = context.snapshot('*.json').entries
    available = sorted({Path(entry.path).name for entry in all_entries
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
        experiments = sorted({record.entry.experiment for record in records})
        chosen = st.multiselect('对比实验（最多 12 个）', experiments, default=experiments[:min(4, len(experiments))],
                                max_selections=12, key=f'trend-exps:{task}')
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
    started = time.perf_counter()
    if 'query' not in st.session_state:
        st.session_state['query'] = initial_query()
    query = st.session_state['query']
    with st.sidebar:
        st.markdown('### ◈ 实验工作台')
        st.caption('评测、比较与回归分析')
        with st.form('global-filter'):
            source = st.radio('数据来源', ['演示数据', '本地目录'], index=0 if query['source'] == '演示数据' else 1)
            root = st.text_input('本地数据根目录', query['root'], placeholder='/data/evaluations')
            keywords = st.text_area('实验关键词', '\n'.join(query['keywords']), height=90,
                                    help='一行一个或用逗号分隔；任一包含即可。空白或 ALL 展示全部。')
            latest = st.toggle('仅展示最新 checkpoint', value=query['only_latest'])
            baseline_latest = st.toggle('置顶实验仅展示最新', value=query['star_only_latest'])
            pin = st.selectbox('置顶标记', ['DEFAULT', *TAGS.values()], index=['DEFAULT', *TAGS.values()].index(query['selected_pin_name']),
                               help='DEFAULT 对应 STAR / SUN。')
            keep_pinned = st.checkbox('关键词筛选时保留基线与置顶实验', query['keep_pinned'])
            iter_first = st.checkbox('checkpoint 显示在实验名前', query['iter_first'])
            applied = st.form_submit_button('应用筛选', type='primary', use_container_width=True)
        if applied:
            words = tuple(dict.fromkeys(word.strip() for word in re.split(r'[,，;；\n]', keywords) if word.strip()))
            query = dict(source=source, root=root.strip(), keywords=words[:100], only_latest=latest,
                         star_only_latest=baseline_latest, keep_pinned=keep_pinned,
                         selected_pin_name=pin, iter_first=iter_first)
            st.session_state['query'] = query
        if st.button('刷新数据', use_container_width=True, help='重新扫描并读取当前视图。其他用户的缓存不受影响。'):
            st.session_state['refresh_token'] = uuid.uuid4().hex
        st.caption('目录快照缓存 10 秒 · 训练日志缓存 60 秒。到期后在下一次交互时更新。')
    st.title('实验性能追踪')
    st.caption('从任务指标出发，定位 checkpoint 差异与性能变化。')
    initial_task = st.query_params.get('task', 'pnc')
    if initial_task not in TASKS:
        initial_task = 'pnc'
    task = st.radio('评测任务', list(TASKS), format_func=TASKS.get,
                    index=list(TASKS).index(initial_task), horizontal=True, key='active-task')
    page = st.radio('工作视图', ['任务指标', '基线对比', 'Checkpoint 趋势', '训练速度'], horizontal=True, key='work-view')
    data_root = DEMO_ROOT if query['source'] == '演示数据' else query['root']
    if query['source'] == '演示数据':
        st.info('当前为模拟评测数据，用于验证交互和功能；不代表真实实验结果。', icon='🧪')
        if not Path(DEMO_ROOT).exists():
            st.info('源码包未包含数据。可生成模拟数据试用，或在左侧选择本地评测目录。')
            if st.button('生成模拟演示数据', key='generate-demo'):
                from scripts.generate_demo import main as generate_demo
                try:
                    generate_demo()
                except (OSError, ValueError, RuntimeError) as error:
                    st.error('模拟数据生成失败：{}'.format(error))
                    return
                st.rerun()
            return
    if not data_root:
        st.info('请在左侧填写数据根目录并应用筛选。')
        return
    context = Context(root=data_root, task=task, keywords=query['keywords'], only_latest=query['only_latest'],
                      star_only_latest=query['star_only_latest'], selected_pin_name=query['selected_pin_name'],
                      keep_pinned=query['keep_pinned'], iter_first=query['iter_first'],
                      refresh_token=st.session_state.get('refresh_token', ''))
    status = st.empty()
    try:
        if page == '任务指标':
            from dashboard.domain import render_task
            render_task(task, context)
        elif page == '训练速度':
            training_view(context)
        else:
            analysis_view(context, task, history=page == 'Checkpoint 趋势')
    except Exception as error:
        context.issue(f'页面渲染失败: {type(error).__name__}: {error}')
        st.error('当前视图未能完整渲染。请查看数据诊断；其他任务仍可正常切换。')
        if os.environ.get('DASHBOARD_DEBUG') == '1':
            st.exception(error)
    requested_patterns = {pattern for pattern, _ in context.loaded}
    entries = {entry.path: entry for pattern, (snapshot, _) in context.snapshots.items()
               if pattern in requested_patterns for entry in snapshot.entries}
    loaded = {record.entry.path: record for records in context.loaded.values() for record in records}
    experiments = {record.entry.experiment for record in loaded.values()}
    checkpoint_count = len({(record.entry.experiment, record.entry.iteration) for record in loaded.values()})
    with status.container():
        a, b, c, d = st.columns(4)
        a.metric('已载入实验', len(experiments))
        b.metric('Checkpoint', checkpoint_count)
        c.metric('结果文件', f'{len(loaded)} / {len(entries)}', help='已载入 / 当前视图索引匹配的文件数')
        d.metric('数据诊断', len(context.messages))
    duration = (time.perf_counter() - started) * 1000
    st.caption(f'本次服务端处理 {duration:.0f} ms · 不含浏览器渲染及网络传输 · 数据源 {query["source"]}')
    if context.snapshots:
        newest = max(timestamp for _, timestamp in context.snapshots.values())
        age = max(0, time.time() - newest)
        st.caption(f'目录快照生成于 {age:.0f} 秒前。新增结果最迟在缓存到期后的下一次交互显示，也可手动刷新。')
    with st.expander(f'数据诊断与来源 · {len(context.messages)} 条', expanded=bool(context.messages)):
        st.code(data_root, language=None)
        for message in context.messages[:100]:
            st.warning(message)
        if len(context.messages) > 100:
            st.caption(f'另有 {len(context.messages) - 100} 条，可在诊断 JSON 中下载。')
        if not context.messages:
            st.caption('本次未发现文件读取或 schema 错误。没有元数据时，任务链接留空。')
        st.caption('最新文件损坏时保留诊断并跳过该结果，不自动用旧 checkpoint 代替。应用对实验目录只读。')
        st.download_button('下载诊断 JSON', json.dumps({'source': data_root, 'task': task, 'issues': context.messages}, ensure_ascii=False, indent=2),
                           'dashboard-diagnostics.json', 'application/json', on_click=None)
    with st.sidebar:
        if st.button('将当前筛选写入分享链接', use_container_width=True):
            st.query_params.update(task=task, q=','.join(query['keywords']), latest='1' if query['only_latest'] else '0', pin=query['selected_pin_name'])
            st.caption('地址栏已更新。链接包含任务与部分筛选；数据目录由接收端部署配置决定。')
        st.download_button('导出视图配置', json.dumps({'task': task, **query}, ensure_ascii=False, indent=2),
                           'dashboard-view.json', 'application/json', use_container_width=True, on_click=None)


if __name__ == '__main__':
    main()
