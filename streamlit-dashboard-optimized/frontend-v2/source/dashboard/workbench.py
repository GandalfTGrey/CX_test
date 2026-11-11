"""Eight-task workbench; all evaluation IO remains in the existing index/loader."""
import datetime as dt
import html
import json
import os
import sqlite3
import time
import uuid

import streamlit as st

from .context import Context, ResultsPending, TAGS
from .controls import control
from .index import Index

CSS = '''<style>
.stApp{background:#f6f8fa;color:#1d3444}
.block-container{padding-top:4rem;padding-bottom:2rem;max-width:1700px}
[data-testid="stVerticalBlock"]{gap:.55rem}
[data-testid="stSidebar"]{background:#eef3f6;border-right:1px solid #dfe7eb}
[data-testid="stSidebarUserContent"]{padding-top:1.5rem}
h1{font-size:1.65rem!important;letter-spacing:-.5px;padding:0 0 .3rem!important;margin:0!important;line-height:1.4!important}
h3{font-size:1.05rem!important}.workbench-subtitle{color:#627987;font-size:12px;margin:10px 0 8px;line-height:1.6}
.st-key-task-nav [role="radiogroup"]{gap:6px;flex-wrap:wrap}
.st-key-task-nav [role="radiogroup"] label{background:#fff;border:1px solid #dce5ea;border-radius:6px;padding:6px 10px;margin:0!important}
.st-key-task-nav [role="radiogroup"] label:has(input:checked){background:#e5f3f0;border-color:#90c8bf;color:#096e64}
.st-key-task-nav [role="radiogroup"] label>div:first-child,.st-key-view-nav [role="radiogroup"] label>div:first-child{position:absolute;width:1px;height:1px;overflow:hidden;opacity:0}
.st-key-task-nav label:has(input:focus-visible),.st-key-view-nav label:has(input:focus-visible){outline:2px solid #137d73;outline-offset:3px}
.st-key-task-nav [role="radiogroup"] label:hover{border-color:#8abfb7}
.st-key-task-nav label p,.st-key-view-nav label p{font-size:12px!important;white-space:nowrap}
.st-key-view-nav [role="radiogroup"]{gap:18px;border-bottom:1px solid #dce5ea;padding:4px 0 0;flex-wrap:wrap}
.st-key-view-nav [role="radiogroup"] label{padding:6px 0 9px;margin:0!important;border-bottom:2px solid transparent}
.st-key-view-nav [role="radiogroup"] label:has(input:checked){border-bottom-color:#0e8175;color:#086f66}
.st-key-query-panel{background:#fff;border-radius:8px}
.st-key-query-panel [data-testid="stVerticalBlockBorderWrapper"]{border-radius:8px}
.st-key-query-panel button{font-weight:550}
.work-status{border:1px solid #dce7e8;border-left:3px solid #168579;background:#eff8f5;border-radius:6px;padding:9px 12px;margin:4px 0 7px;font-size:13px;line-height:1.6}
.work-status .detail{font-size:11px;color:#597483;margin-top:2px;overflow-wrap:anywhere}
.work-status.loading{background:#eff5fa;border-color:#d3e2ed;border-left-color:#4b819e}
.work-status.empty{background:#fff;border-color:#dfe6eb;border-left-color:#91a4b0}
.work-status.warning{background:#fff8eb;border-color:#eadcbd;border-left-color:#ab7b24}
.work-status.error{background:#fff3f2;border-color:#edd6d3;border-left-color:#b1574b}
div.stButton>button,div.stDownloadButton>button{border-radius:6px;font-size:.82rem}
[data-testid="stDataFrame"]{border:1px solid #dce5ea;border-radius:7px}
[data-testid="stCaptionContainer"]{line-height:1.55}
[data-testid="stCaptionContainer"] p{color:#627886;font-size:12px}
@media(max-width:700px){
 .block-container{padding-top:4rem;padding-left:1rem;padding-right:1rem}
 h1{font-size:1.4rem!important}.workbench-subtitle{margin:10px 0 10px}
 .st-key-task-nav [role="radiogroup"]{display:grid;grid-template-columns:1fr 1fr;gap:5px}
 .st-key-task-nav [role="radiogroup"] label{padding:5px 7px;min-width:0}
 .st-key-query-panel [data-testid="stHorizontalBlock"]{display:grid;grid-template-columns:1fr 1fr;gap:8px}
 .st-key-query-panel [data-testid="stColumn"]{width:100%!important;min-width:0!important}
 .st-key-query-panel [data-testid="stColumn"]:first-child,.st-key-query-panel [data-testid="stColumn"]:last-child{grid-column:1/-1}
}
</style>'''


def words(value):
    import re
    return tuple(dict.fromkeys(word.strip() for word in re.split(r'[,，;；\n]', value) if word.strip()))[:100]


def query_label(query):
    text = ' / '.join(query.get('keywords', ())) or '全部实验'
    checkpoint = '历史 checkpoint' if query.get('history') else '最新 checkpoint' if query.get('only_latest') else '全部 checkpoint'
    return '{} · {} · {}'.format(text, checkpoint,
                                '保留基线' if query.get('keep_pinned') else '基线随筛选')


def stamp(value):
    zone = dt.timezone(dt.timedelta(hours=8))
    return dt.datetime.fromtimestamp(value, zone).strftime('%m-%d %H:%M:%S') if value else '尚未就绪'


def panel(slot, kind, title, detail=''):
    slot.markdown('<div class="work-status {}" role="status" aria-live="polite"><strong>{}</strong><div class="detail">{}</div></div>'.format(
        html.escape(kind), html.escape(title), html.escape(detail)), unsafe_allow_html=True)


def reset_operation():
    st.session_state['result_page'] = 0
    st.session_state['operation_notice'] = ''
    st.session_state['read_paused'] = None


def run(initial_query, analysis_view, training_view, tasks):
    ss = st.session_state
    if 'indexed_session' not in ss:
        ss.update(indexed_session=uuid.uuid4().hex, request_query=initial_query(), applied_query=None,
                  previous_results={}, result_page=0, read_nonce='', work_busy=True,
                  pagination_size=50, result_views={})
    ss.setdefault('result_views', {})
    ss.setdefault('pagination_size', 50)
    query = ss.request_query
    defaults = {'draft-root': query['root'], 'draft-words': ', '.join(query['keywords']),
                'draft-latest': query['only_latest'], 'draft-baseline-latest': query['star_only_latest'],
                'draft-pin': query['selected_pin_name'], 'draft-keep': query['keep_pinned'], 'draft-iter': query['iter_first']}
    for key, value in defaults.items():
        if key not in ss:
            ss[key] = value
    st.markdown(CSS, unsafe_allow_html=True)
    with st.sidebar:
        st.markdown('### ◈ 实验工作台')
        st.caption('模型评测 / 本地与共享挂载目录')
        root = st.text_input('评测数据根目录', placeholder='/data/evaluations', key='draft-root')
        st.caption('填写服务端可访问的目录，在主区应用筛选。')
        with st.expander('基线与显示设置'):
            keep = st.checkbox('关键词筛选时保留基线与置顶实验', key='draft-keep')
            baseline_latest = st.toggle('置顶实验仅展示最新', key='draft-baseline-latest')
            pin = st.selectbox('置顶标记', ['DEFAULT', *TAGS.values()], key='draft-pin')
            iter_first = st.checkbox('checkpoint 显示在实验名前', key='draft-iter')
        st.divider()
        st.caption('筛选和任务切换复用本地索引。刷新数据时才重新发现文件，实验目录只读。')

    st.title('实验性能追踪')
    st.markdown('<div class="workbench-subtitle">按任务查看指标、固定基线，追踪 checkpoint 变化。</div>', unsafe_allow_html=True)
    initial_task = st.query_params.get('task', 'pnc')
    if initial_task not in tasks:
        initial_task = 'pnc'
    with st.container(key='task-nav'):
        task = st.radio('评测任务', list(tasks), format_func=tasks.get, index=list(tasks).index(initial_task),
                        horizontal=True, key='active-task', label_visibility='collapsed', on_change=reset_operation)
    with st.container(key='view-nav'):
        view = st.radio('工作视图', ['任务指标', '基线对比', 'Checkpoint 趋势', '训练速度'], horizontal=True,
                        key='work-view', label_visibility='collapsed', on_change=reset_operation)
    if view == '训练速度':
        ss.work_busy = False
        st.caption('训练日志独立查询，不使用评测关键词或结果分页。')
        training_view(Context(root=query['root'], task=task, view=view, refresh_token=ss.read_nonce))
        return

    with st.container(border=True, key='query-panel'):
        left, middle, size, action = st.columns([5, 2, 1.6, 1.6], vertical_alignment='bottom')
        text = left.text_input('实验关键词', placeholder='实验名；多个关键词用逗号分隔', key='draft-words')
        latest = middle.toggle('仅展示最新 checkpoint', key='draft-latest', disabled=view == 'Checkpoint 趋势',
                               help='趋势按所选实验查看历史 checkpoint；此开关影响指标和基线对比视图。')
        with size:
            def resize():
                ss.pagination_size = ss['page-size']
                reset_operation()
            control('selectbox', '每页数量', [10, 25, 50, 100], index=[10, 25, 50, 100].index(ss.pagination_size),
                    key='page-size', on_change=resize)
        draft = dict(source='本地目录', root=root.strip(), keywords=words(text), only_latest=latest,
                     star_only_latest=baseline_latest, keep_pinned=keep, selected_pin_name=pin, iter_first=iter_first)
        if action.button('应用筛选', type='primary', use_container_width=True):
            ss.request_query = draft
            reset_operation()
            ss.work_busy = True
        if draft != ss.request_query:
            st.caption('有未应用的修改 · 表格条件保持原样')
    query = ss.request_query
    if not query['root']:
        panel(st.empty(), 'empty', '先设置评测数据目录', '在侧栏填写真实结果根目录，再点击“应用筛选”。此页面不会创建任何示例数据。')
        ss.work_busy = False
        return
    try:
        index = Index(query['root'])
        index.start()
    except (OSError, RuntimeError, sqlite3.Error) as error:
        st.error('本地索引无法打开：{}。请检查可写的本地索引目录。'.format(error))
        return
    root = index.root
    view_key = (root, task, view)
    result_view = ss.result_views.setdefault(view_key, {})

    @st.fragment(run_every=1 if ss.get('work_busy', True) else None)
    def workspace():
        state = index.state()
        scanning = state['status'] in ('starting', 'scanning', 'stalled')
        source, refresh, stop = st.columns([6, 1.2, 1.2], vertical_alignment='center')
        source.caption('数据源：{} · 索引 {}（UTC+8）'.format(os.path.basename(root) or root, stamp(state['updated'])))
        if refresh.button('刷新数据', key='index-refresh', disabled=scanning, use_container_width=True):
            index.start(force=True)
            ss.read_nonce = uuid.uuid4().hex
            ss.work_busy = True
            ss.operation_notice = ''
            ss.read_paused = None
            st.rerun()
        if scanning and stop.button('取消扫描', key='index-cancel', use_container_width=True):
            index.cancel()
            ss.operation_notice = '已请求取消扫描，等待当前 NFS 操作返回。'
        status = st.empty()
        if state['status'] in ('failed', 'cancelled', 'stalled'):
            st.warning(state.get('message', '本次扫描已停止，旧索引仍保留。'))
        if scanning:
            st.caption('发现目录：{:,} 个 · 结果：{:,} 个 · 耗时 {:.1f} 秒；保留上一完整索引。'.format(
                state.get('directories', 0), state.get('files', 0), time.time() - state.get('started', time.time())))
        if not state['active']:
            panel(status, 'loading' if scanning else 'warning', '正在建立首次索引' if scanning else '尚无可用索引',
                  '进度每秒更新，界面可继续操作。' if scanning else '检查数据源后点击“刷新数据”重试。')
            busy = scanning
        elif ss.get('read_paused') == view_key:
            panel(status, 'empty', '首次读取已取消', '当前 NFS 操作返回后停止；可重试读取或重新应用筛选。')
            if st.button('重试读取', key='retry-initial'):
                ss.read_paused = None
                ss.read_nonce = uuid.uuid4().hex
                ss.work_busy = True
                ss.operation_notice = ''
                st.rerun()
            busy = scanning
        else:
            context = Context(root=root, task=task, view=view, keywords=query['keywords'],
                              only_latest=query['only_latest'], star_only_latest=query['star_only_latest'],
                              selected_pin_name=query['selected_pin_name'], keep_pinned=query['keep_pinned'],
                              iter_first=query['iter_first'], refresh_token=ss.read_nonce, index=index, batch=state['active'],
                              page=ss.result_page, page_size=ss.pagination_size, asynchronous=True,
                              session=ss.indexed_session, previous=ss.previous_results)
            try:
                if view == '任务指标':
                    from .domain import render_task
                    render_task(task, context)
                else:
                    analysis_view(context, task, history=view == 'Checkpoint 趋势')
            except ResultsPending:
                pass
            except Exception as error:
                context.failed = True
                context.issue('{}: {}'.format(type(error).__name__, error))
                if os.environ.get('DASHBOARD_DEBUG') == '1':
                    st.exception(error)
            ss.result_page = context.page
            records = {record.entry.path: record for values in context.loaded.values() for record in values}
            total = max(context.totals.values(), default=0)
            pages = max(1, (total + context.page_size - 1) // context.page_size)
            shown = context.displayed[0] if context.displayed else None
            description = ''
            if shown:
                info, timestamp = shown
                description = '{} · 第{}页 / 每页{} · {} · 版本就绪于 {}（UTC+8）'.format(
                    info['pattern'], info['page'] + 1, info['page_size'], query_label(info), stamp(timestamp))
            if context.pending:
                count = sum(job.count for job in context.pending)
                files = sum(len(job.entries) for job in context.pending)
                elapsed = max(time.monotonic() - job.started for job in context.pending)
                panel(status, 'loading', '正在准备所选结果 · {} / {} 文件 · {:.1f} 秒'.format(count, files, elapsed),
                      '当前保留旧结果：' + description if context.preserved else '首次读取中，完成后展示；这不是空结果。')
                if st.button('取消本次读取', key='cancel-read'):
                    for job in context.pending:
                        job.cancelled.set()
                    old = result_view.get('success')
                    if old:
                        ss.request_query = old['query'].copy()
                        ss.result_page = old['page']
                        ss.read_nonce = old['nonce']
                    else:
                        ss.read_paused = view_key
                    ss.operation_notice = '本次读取已取消；待应用输入保持不变。'
                    ss.work_busy = False
                    st.rerun()
            elif context.failed:
                panel(status, 'error', '本次读取失败 · 保留旧结果' if context.preserved else '本次结果未能准备完成',
                      description or '查看诊断，修正数据源或重试；不会冒充空结果或更换基线。')
                if st.button('重试本次读取', key='retry-read'):
                    ss.read_nonce = uuid.uuid4().hex
                    ss.operation_notice = ''
                    ss.work_busy = True
                    index.start(force=True)
                    st.rerun()
            elif not context.requested:
                panel(status, 'empty', '请选择可用评测项', '当前视图尚未读取结果；上方说明和选项可帮助定位文件或格式。')
            elif total == 0:
                panel(status, 'empty', '没有匹配的结果', query_label(query) + '；可清空关键词或检查评测文件。')
                ss.applied_query = query.copy()
                result_view['success'] = dict(query=query.copy(), page=context.page, nonce=ss.read_nonce)
            else:
                kind = 'warning' if context.messages else ''
                panel(status, kind, '本页 {} 个结果文件 · 查询匹配 {:,} 个实验 / checkpoint{}'.format(
                    len(records), total, ' · 部分结果有诊断' if context.messages else ''), description)
                ss.applied_query = query.copy()
                result_view['success'] = dict(query=query.copy(), page=context.page, nonce=ss.read_nonce)
            if ss.get('operation_notice'):
                st.caption(ss.operation_notice)
            if context.requested:
                previous, summary, following = st.columns([1, 5, 1], vertical_alignment='center')
                if previous.button('上一页', disabled=context.page == 0 or bool(context.pending), key='page-prev'):
                    ss.result_page = context.page - 1
                    ss.operation_notice = ''
                    st.rerun()
                summary.caption('第 {} / {} 页 · 每页 {} · 额外参考基线 {} · 排序和 CSV 作用于本页'.format(
                    context.page + 1, pages, context.page_size, len(context.extra_references)))
                if following.button('下一页', disabled=context.page + 1 >= pages or bool(context.pending), key='page-next'):
                    ss.result_page = context.page + 1
                    ss.operation_notice = ''
                    st.rerun()
            with st.expander('数据诊断与来源 · {} 条'.format(len(context.messages)), expanded=bool(context.messages)):
                st.code(root, language=None)
                for message in context.messages[:50]:
                    st.warning(message)
                st.caption('结果缓存按文件指纹复用；版本就绪时间不代表每次交互重新读取 NFS。实验目录只读。')
                st.download_button('下载诊断 JSON', json.dumps({'task': task, 'view': view, 'index': state,
                                   'issues': context.messages}, ensure_ascii=False, indent=2),
                                   'dashboard-diagnostics.json', 'application/json', on_click=None)
            busy = scanning or bool(context.pending)
        if ss.get('work_busy') != busy:
            ss.work_busy = busy
            st.rerun()

    workspace()
