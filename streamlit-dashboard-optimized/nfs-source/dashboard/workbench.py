"""Streamlit 1.40 workbench: preserve eight task interfaces, expose real work."""
import html
import json
import os
from pathlib import Path
import re
import sqlite3
import time
import uuid

import streamlit as st

from .context import Context, TAGS
from .index import Index

CSS = '''<style>
.stApp{background:#f7f9fb;color:#1c3445}
.block-container{padding-top:4.2rem;padding-bottom:2rem;max-width:1800px}
[data-testid="stVerticalBlock"]{gap:.6rem}
[data-testid="stSidebarUserContent"]{padding-top:1.5rem}
h1{padding-top:0!important;padding-bottom:.3rem!important}
[data-testid="stSidebar"]{background:#eef3f6;border-right:1px solid #dce5ea}
h1{font-size:1.75rem!important;letter-spacing:-.6px}h3{font-size:1rem!important}
[data-testid="stMetric"]{padding:8px;border:0}
div.stButton>button,div.stDownloadButton>button{border-radius:6px;font-size:.84rem}
[data-testid="stRadio"]>div{gap:.35rem 1.2rem;flex-wrap:wrap}
[data-testid="stRadio"] label p{font-size:.85rem}
.source{background:white;border:1px solid #dce6eb;border-radius:8px;padding:9px 13px;margin:0}
.source b{color:#244956}.muted{color:#718894;font-size:12px}
.query-state{border-left:3px solid #128878;background:#eef7f5;padding:10px 14px;margin:8px 0 14px;font-size:13px}
.section-intro{color:#758b98;font-size:12px;margin-top:-12px;margin-bottom:14px}
[data-testid="stStatusWidget"]{font-size:13px}
[data-testid="stDataFrame"]{border:1px solid #dce6eb;border-radius:7px}
div[data-testid="stVerticalBlockBorderWrapper"]>div{border-radius:8px}
</style>'''


def words(value):
    return tuple(dict.fromkeys(word.strip() for word in re.split(r'[,，;；\n]', value) if word.strip()))[:100]


def query_label(query):
    text = ' / '.join(query['keywords']) or '全部实验'
    return '{} · {} · {}'.format(text, '最新 checkpoint' if query['only_latest'] else '全部 checkpoint',
                                '筛选时保留基线' if query['keep_pinned'] else '基线随筛选')


def run(initial_query, analysis_view, training_view, tasks, demo_root):
    ss = st.session_state
    if 'indexed_session' not in ss:
        ss.indexed_session = uuid.uuid4().hex
        ss.request_query = initial_query()
        ss.applied_query = None
        ss.previous_results = {}
        ss.result_page = 0
        ss.read_nonce = ''
        ss.work_busy = True
    current = ss.request_query
    defaults = {'draft-source': current['source'], 'draft-root': current['root'],
                'draft-words': ', '.join(current['keywords']), 'draft-latest': current['only_latest'],
                'draft-baseline-latest': current['star_only_latest'], 'draft-pin': current['selected_pin_name'],
                'draft-keep': current['keep_pinned'], 'draft-iter': current['iter_first']}
    for key, value in defaults.items():
        if key not in ss:
            ss[key] = value
    st.markdown(CSS, unsafe_allow_html=True)
    with st.sidebar:
        st.markdown('### ◈ 实验工作台')
        st.caption('模型评测 / 数据来源')
        source = st.radio('数据来源', ['演示数据', '本地目录'], key='draft-source')
        root = st.text_input('本地数据根目录', placeholder='/data/evaluations', key='draft-root')
        st.caption('NFS 或共享挂载路径直接填写在这里。')
        baseline_latest = st.toggle('置顶实验仅展示最新', key='draft-baseline-latest')
        pin = st.selectbox('置顶标记', ['DEFAULT', *TAGS.values()],
                          key='draft-pin')
        keep = st.checkbox('关键词筛选时保留基线与置顶实验', key='draft-keep')
        iter_first = st.checkbox('checkpoint 显示在实验名前', key='draft-iter')
        st.divider()
        st.caption('目录由后台扫描建立本地索引。普通筛选和任务切换复用索引；刷新数据后才重新发现文件。')
        st.caption('每页读取预算 32 MiB；单文件超过 16 MiB 单独报告。')

    st.title('实验性能追踪')
    st.markdown('<div class="section-intro">查看任务指标，固定基线，追踪 checkpoint 变化。</div>', unsafe_allow_html=True)
    initial_task = st.query_params.get('task', 'pnc')
    if initial_task not in tasks:
        initial_task = 'pnc'
    task = st.radio('评测任务', list(tasks), format_func=tasks.get,
                    index=list(tasks).index(initial_task), horizontal=True, key='active-task',
                    label_visibility='collapsed',
                    on_change=lambda: ss.update(result_page=0))
    page = st.radio('工作视图', ['任务指标', '基线对比', 'Checkpoint 趋势', '训练速度'],
                    label_visibility='collapsed',
                    horizontal=True, key='work-view', on_change=lambda: ss.update(result_page=0))
    with st.container(border=True):
        left, middle, right = st.columns([5, 2, 1.3])
        keyword_text = left.text_input('实验关键词',
                                       placeholder='输入实验名；多个关键词用逗号分隔', key='draft-words')
        latest = middle.toggle('仅展示最新 checkpoint', key='draft-latest')
        submitted = right.button('应用筛选', type='primary', use_container_width=True)
        draft = dict(source=source, root=root.strip(), keywords=words(keyword_text), only_latest=latest,
                     star_only_latest=baseline_latest, keep_pinned=keep,
                     selected_pin_name=pin, iter_first=iter_first)
        if submitted:
            ss.request_query = draft
            ss.result_page = 0
            ss.work_busy = True
            ss.request_cancelled = False
            ss.initial_read_cancelled = False
        if draft != ss.request_query:
            st.caption('🟠 有未应用的修改 · 点击“应用筛选”后执行，当前表格条件保持原样。')
        elif ss.applied_query != ss.request_query:
            st.caption('🔵 条件已提交 · 读取成功后更新当前结果；加载状态显示在下方。')
        else:
            st.caption('✓ 条件已应用 · {}'.format(query_label(ss.applied_query)))
    query = ss.request_query
    data_root = demo_root if query['source'] == '演示数据' else query['root']
    if not data_root:
        st.info('填写数据根目录并应用筛选后，开始后台目录发现。')
        return
    if query['source'] == '演示数据':
        st.caption('🧪 演示数据 · 模拟评测结果，不代表真实实验。')
        if not Path(demo_root).exists():
            if st.button('生成模拟演示数据'):
                from scripts.generate_demo import main as generate_demo
                generate_demo()
                st.rerun()
            return
    try:
        index = Index(data_root)
        index.start()
    except (OSError, RuntimeError, sqlite3.Error) as error:
        st.error('本地索引无法打开：{}。请将 DASHBOARD_INDEX_DIR 指向可写的本地磁盘。'.format(error))
        return

    @st.fragment(run_every=1 if ss.get('work_busy', True) else None)
    def workspace():
        state = index.state()
        scanning = state['status'] in ('starting', 'scanning', 'stalled')
        age = time.strftime('%m-%d %H:%M:%S', time.localtime(state['updated'])) if state['updated'] else '尚未完成'
        status = st.empty()
        a, b, c = st.columns([6, 1.3, 1.3])
        a.markdown('<div class="source"><b>{}</b> <span class="muted">· 本地索引 {} · {}</span></div>'.format(
            html.escape(query['source']), age, '正在更新' if scanning else '可查询' if state['active'] else '等待索引'), unsafe_allow_html=True)
        if b.button('刷新数据', key='index-refresh', use_container_width=True, disabled=scanning):
            index.start(force=True)
            ss.read_nonce = uuid.uuid4().hex
            ss.work_busy = True
            ss.initial_read_cancelled = False
            st.rerun()
        if scanning and c.button('取消扫描', key='index-cancel', use_container_width=True):
            index.cancel()
            st.info('已请求取消；等待当前 NFS 操作返回，旧索引仍可查询。')
        if state['status'] in ('failed', 'cancelled', 'stalled'):
            st.warning(state.get('message', '扫描已停止，旧索引保留。'))
        if not state['active']:
            with status.container():
                if scanning:
                    st.info('正在建立首次索引 · 已扫描 {:,} 个目录 · 发现 {:,} 个结果 · 已耗时 {:.1f} 秒。页面每秒更新。'.format(
                        state.get('directories', 0), state.get('files', 0), time.time() - state.get('started', time.time())))
                else:
                    st.warning('尚无可用索引；请检查数据目录或点击“刷新数据”重试。')
            busy = scanning
        else:
            if scanning:
                st.info('后台发现目录：{:,} 个目录 / {:,} 个结果 · {:.1f} 秒。继续显示上次完成的索引和结果。'.format(
                    state.get('directories', 0), state.get('files', 0), time.time() - state.get('started', time.time())))
            if ss.get('initial_read_cancelled'):
                st.info('首次指标读取已取消；等待中的 NFS 调用返回后停止工作。点击重试或重新应用筛选。')
                if st.button('重试首次读取', key='retry-initial'):
                    ss.initial_read_cancelled = False
                    ss.read_nonce = uuid.uuid4().hex
                    ss.work_busy = True
                    st.rerun()
                if ss.work_busy:
                    ss.work_busy = False
                    st.rerun()
                return
            context = Context(root=data_root, task=task, keywords=query['keywords'], only_latest=query['only_latest'],
                              star_only_latest=query['star_only_latest'], selected_pin_name=query['selected_pin_name'],
                              keep_pinned=query['keep_pinned'], iter_first=query['iter_first'],
                              refresh_token=ss.read_nonce, index=index, batch=state['active'],
                              page=ss.result_page, page_size=ss.get('page-size', 50), asynchronous=True,
                              session=ss.indexed_session, previous=ss.previous_results)
            # Show the committed conditions before invoking any task renderer.
            applied = ss.applied_query
            current_conditions = st.empty()
            load_status = st.empty()
            controls = st.empty()
            try:
                if page == '任务指标':
                    from .domain import render_task
                    render_task(task, context)
                elif page == '训练速度':
                    training_view(context)
                else:
                    analysis_view(context, task, history=page == 'Checkpoint 趋势')
            except Exception as error:
                context.failed = True
                context.issue('{}: {}'.format(type(error).__name__, error))
                st.error('当前视图渲染失败，错误详情见下方诊断。')
                if os.environ.get('DASHBOARD_DEBUG') == '1':
                    st.exception(error)
            if context.pending:
                current_conditions.caption('当前表格：{} · 第{}页；新条件尚未生效。'.format(
                    query_label(applied) if applied else '首次读取中', ss.get('applied_page', 0) + 1))
                count = sum(job.count for job in context.pending)
                total = sum(len(job.entries) for job in context.pending)
                elapsed = max(time.monotonic() - job.started for job in context.pending)
                load_status.info('读取所选指标 · {} / {} 个文件 · {:.1f} 秒 · {}'.format(
                    count, total, elapsed, '保留上一份成功结果，新条件尚未生效。' if context.preserved else '首次读取中，完成后展示结果。'))
                with controls.container():
                    if st.button('取消本次读取', key='cancel-read'):
                        for job in context.pending:
                            job.cancelled.set()
                        if ss.applied_query is not None:
                            ss.request_query = ss.applied_query
                            ss.result_page = ss.get('applied_page', 0)
                        else:
                            ss.initial_read_cancelled = True
                        ss.read_nonce = ss.get('applied_nonce', '')
                        ss.work_busy = False
                        ss.request_cancelled = True
                        st.rerun()
            elif context.failed:
                current_conditions.caption('保留结果的条件：{}'.format(query_label(applied) if applied else '尚无成功结果'))
                load_status.error('本次读取失败，已保留上一份成功结果；新条件尚未生效。请查看诊断并重试。')
                with controls.container():
                    if st.button('重试本次读取', key='retry-read'):
                        ss.read_nonce = uuid.uuid4().hex
                        index.start(force=True)
                        ss.work_busy = True
                        st.rerun()
            else:
                ss.applied_query = query.copy()
                ss.applied_page = ss.result_page
                ss.applied_nonce = ss.read_nonce
                records = {record.entry.path: record for values in context.loaded.values() for record in values}
                load_status.success('✓ 条件已生效 · 本页 {} 个结果文件 · {} 个实验{}'.format(
                    len(records), len({record.entry.experiment for record in records.values()}),
                    ' · {} 条诊断（部分文件未能展示）'.format(len(context.messages)) if context.messages else ''))
                latest_read = max((item[1] for key, item in ss.previous_results.items()
                                   if key[0] == data_root and key[1] == task), default=0)
                current_conditions.caption('结果读取于 {} · 第{}页 · {}'.format(
                    time.strftime('%H:%M:%S', time.localtime(latest_read)) if latest_read else '尚无读取',
                    ss.result_page + 1, query_label(query)))
            if ss.get('request_cancelled'):
                st.caption('本次更新已取消；输入框保留待应用条件，旧结果继续显示。')
            total = max(context.totals.values(), default=0)
            pages = max(1, (total + context.page_size - 1) // context.page_size)
            prev, indicator, nxt, size = st.columns([1, 4, 1, 2])
            if prev.button('上一页', disabled=ss.result_page == 0, key='page-prev'):
                ss.result_page -= 1
                st.rerun()
            indicator.caption('匹配 {:,} 个实验 / checkpoint · 第 {} / {} 页 · 本页另保留最多8个置顶基线'.format(total, ss.result_page + 1, pages))
            if nxt.button('下一页', disabled=ss.result_page + 1 >= pages, key='page-next'):
                ss.result_page += 1
                st.rerun()
            size.selectbox('每页数量', [10, 25, 50, 100], index=2, key='page-size',
                           on_change=lambda: ss.update(result_page=0))
            with st.expander('数据诊断与来源 · {} 条'.format(len(context.messages)), expanded=bool(context.messages)):
                st.code(data_root, language=None)
                for message in context.messages[:50]:
                    st.warning(message)
                st.caption('单文件错误隔离；没有自动用旧 checkpoint 替代损坏的新结果。旧表保留时，以状态区条件为准。')
                st.download_button('下载诊断 JSON', json.dumps({'task': task, 'index': state,
                                   'issues': context.messages}, ensure_ascii=False, indent=2),
                                   'dashboard-diagnostics.json', 'application/json', on_click=None)
            busy = scanning or bool(context.pending)
        if ss.get('work_busy') != busy:
            ss.work_busy = busy
            st.rerun()

    workspace()
