"""Compatibility renderers for the eight existing metric families.

Domain formulas retain the uploaded dashboard's conventions. Each render builds a
private closure; no mutable user choices or metric parameters live in module globals.
"""
from __future__ import annotations

import hashlib
import numpy as np
import streamlit as streamlit
from st_aggrid import DataReturnMode, GridUpdateMode
from st_aggrid.shared import JsCode
from .controls import control
from .context import ResultsPending


class _Widgets:
    """Give legacy controls stable task-scoped identities."""
    _controls = {'selectbox', 'slider', 'text_area', 'form', 'toggle', 'multiselect'}

    def __init__(self, task):
        self.task = task

    def __getattr__(self, name):
        target = getattr(streamlit, name)
        if name not in self._controls:
            return target
        def call(*args, **kwargs):
            label = kwargs.get('label', args[0] if args else name)
            identity = str(kwargs.pop('key', label))
            digest = hashlib.sha1(identity.encode()).hexdigest()[:12]
            kwargs['key'] = f'domain:{self.task}:{name}:{digest}'
            if name == 'form':
                return target(*args, **kwargs)
            label = kwargs.pop('label', args[0] if args else name)
            return control(name, label, *args[1:], reset_page=True, **kwargs)
        return call


def render_task(task: str, context):
    st = _Widgets(task)
    iter_first = context.iter_first
    only_latest = '是' if context.only_latest else '否'
    AgGrid = context.grid
    schemas = {}

    def pills(label, options, index=0, key=None):
        options = list(options)
        index = max(0, min(index or 0, len(options) - 1))
        identity = key or label
        value = control('radio',
            label, options, index=index, horizontal=True, label_visibility='collapsed',
            key=f'domain:{task}:pills:{identity}', reset_page=True)
        return value if value in options else options[index]

    def read_eval_rslts(pattern, _latest=None, **kwargs):
        return context.read_results(pattern, **kwargs)

    def collect_line(lines, fields, columns):
        columns = list(columns)
        values = list(fields[3:-1])
        if len(values) == len(columns) + 1 and 'roma' not in columns:
            columns.append('roma')
        if len(values) != len(columns):
            context.issue(f'{fields[1]} @ {fields[2]}：列数不一致，已跳过该行，避免指标错位')
            return
        schemas.setdefault(id(lines), []).append((list(fields[:3]), dict(zip(columns, values)), fields[-1]))
        lines.append(list(fields))

    def prepare_lines_and_table(lines, header, head_width=350):
        captured = schemas.get(id(lines), [])
        if captured:
            union = list(header or ['Exp'])
            for _, values, _ in captured:
                for column in values:
                    if column not in union:
                        union.append(column)
            header[:] = union
            lines = [prefix + [values.get(column, '—') for column in union] + [flag]
                     for prefix, values, flag in captured]
        return context.prepare_table(lines, header, head_width)

    def config_url(gb):
        gb.configure_column('roma', headerName='任务链接', width=90, cellRenderer=JsCode('''
        class SafeLink {
          init(params) {
            this.eGui = document.createElement('span');
            try {
              const link = new URL(params.value);
              if (!['http:', 'https:'].includes(link.protocol)) return;
              const anchor = document.createElement('a');
              anchor.textContent = '打开任务';
              anchor.href = link.href;
              anchor.target = '_blank';
              anchor.rel = 'noopener noreferrer';
              this.eGui.appendChild(anchor);
            } catch (_) {}
          }
          getGui() { return this.eGui; }
        }
        '''))
        return gb

    def show_tensorboard(_text):
        st.info('请使用「Checkpoint 趋势」中的趋势图。旧 TensorBoard 导出涉及修改实验目录，尚未迁移到本面板。')

    def eval_and_vis_bev_model():
        st.info('此功能依赖未提供的 eval_and_vis_bev_model.py；请通过基线对比查看原始评测数据。')

    def _finish_rows(rows, widths=130):
        if not rows:
            st.info('当前筛选下没有可展示的评测数据。')
            return
        header = ['Exp']
        for _, values, _ in rows:
            for column in values:
                if column not in header:
                    header.append(column)
        lines = []
        for prefix, values, flag in rows:
            lines.append(prefix + [values.get(column, '—') for column in header] + [flag])
        data, pinned, gb = context.prepare_table(lines, header, 350)
        for column in header[1:]:
            gb.configure_column(field=column, header_name=column, width=widths)
        if 'roma' in header:
            config_url(gb)
        options = gb.build()
        options['pinnedTopRowData'] = pinned
        context.grid(data, gridOptions=options, height=min(800, max(180, 62 + 29 * (len(data) + len(pinned)))), allow_unsafe_jscode=True)

    def _prefix(exp, iteration):
        label = f'{get_iter_num(iteration)}@{exp}' if iter_first else f'{exp}@{get_iter_num(iteration)}'
        return [0, exp, iteration], label

    def _ratio(value):
        a, b = str(value).split('/')
        return float(a) / float(b) if float(b) else 0.0

    def _detector_table(results, selection, new=False):
        records, metadata = results
        item = selection.get('metric_item', '')
        metric = selection.get('metric', '')
        region = selection.get('far_distance', '')
        if item == 'Whitelist':
            context.issue('原代码的 Whitelist 表缺少指标解析；请选择基线对比查看 result.json 的原始字段。')
            st.info('Whitelist 文件可在基线对比中查看；需要业务 schema 后才能恢复专用表。')
            return
        distances = ({'前向近距离': ['30', '60', '90', '150'], '侧后向近距离': ['3', '10', '45', '80'],
                      '其他区域': ['cipv', 'vision', 'lidar'],
                      '侧向远距离': ['lat_-80_-45', 'lat_-45_-30', 'lat_-30_-15', 'lat_-15_0', 'lat_0_15', 'lat_15_30', 'lat_30_45', 'lat_45_80']}
                     if new else {'前向近距离': [90, 60, 30], '侧后向近距离': [40, 10, 5, 4, 3, 0],
                                  '前向远距离分区间': [150, 120, 90, 60, 30], '后向远距离分区间': [110, 90, 60, 30], '动静': [90, 60, 30]})
        rows = []
        for (exp, iteration), payload in records.items():
            prefix, label = _prefix(exp, iteration)
            values = {'Exp': label}
            try:
                if item == 'Ghost':
                    data = payload[0] if isinstance(payload, list) else payload
                    values.update({str(k): str(v) for k, v in data.items()})
                elif item == 'CutIn':
                    for key, value in payload.items():
                        if key == 'average_latency' or value == '':
                            values[key] = value or '—'
                        elif metric == '是':
                            values[key] = f"{value['latency']}({value['gt_frame']},{value['pred_frame']})"
                        else:
                            values[key] = value['latency']
                elif item == 'CutIn_Latency':
                    for tag, value in payload.items():
                        for name in ('mean', 'max', 'median', 'failed_num'):
                            values[f'{tag}_{name}'] = value.get(name, '—')
                elif item == 'Detect':
                    for index, (category, short) in enumerate(zip(
                        ['CAR', 'TRUCK', 'PEDESTRIAN', 'CYCLIST', 'HEAD', 'CONETANK'],
                        ['car', 'truck', 'ped', 'cyc', 'head', 'cone'])):
                        if selection.get('noshow_headcone') == '是' and index >= (5 if new else 4):
                            continue
                        root = payload.get(category, {}).get(selection.get('iou_opt'), {})
                        thresholds = root.get('dist_thres', [])
                        normalized_thresholds = [str(value) for value in thresholds]
                        for distance in distances.get(region, []):
                            if not new and region == '侧后向近距离' and short != 'ped' and distance in (5, 4):
                                continue
                            suffix = str(distance).replace('lat_', '').replace('_', '~')
                            column = f'{short}({suffix}{"" if region == "其他区域" else "m"})'
                            values[column] = '—'
                            if str(distance) not in normalized_thresholds:
                                if root:
                                    continue
                                context.issue(f'{exp}@{iteration}：缺少 {category}/{selection.get("iou_opt")}')
                                continue
                            location = normalized_thresholds.index(str(distance))
                            try:
                                if metric in ('ap(p,r)(value)', '(p,r)(value)'):
                                    precision = root['precision']['I_BEV'][location]
                                    recall = root['recall']['I_BEV'][location]
                                    pair = f'({_ratio(precision)*100:.1f},{_ratio(recall)*100:.1f})({precision},{recall})'
                                    values[column] = f"{root['ap']['I_BEV'][location]}{pair}" if not new else pair
                                elif metric == 'velocity_error(vel_degree)':
                                    values[column] = f"{root['velocity_error']['I_BEV'][location]}({root['velocity_degree']['I_BEV'][location]})"
                                elif new and metric == 'ap':
                                    values[column] = f"{np.asarray(root['thr_pre']['I_BEV'][location], dtype=float).mean()*100:.1f}"
                                elif new and metric == 'velocity_error':
                                    split = root['velocity_error_mean']['I_BEV'][location]
                                    values[column] = '{} ({}, {}, {})'.format(root[metric]['I_BEV'][location], split['v<=1.5m/s'], split['1.5m/s<v<=8m/s'], split['v>8m/s'])
                                elif new and metric in ('moving_pr', 'stationary_pr'):
                                    p, r, tp, fp, fn = root[metric]['I_BEV'][location].split(',')
                                    tp, fp, fn = int(tp), int(fp), int(fn)
                                    values[column] = f'({float(p)*100:.2f},{float(r)*100:.2f})({tp}/{tp+fp},{tp}/{tp+fn})'
                                else:
                                    values[column] = root[metric]['I_BEV'][location]
                            except (KeyError, TypeError, IndexError, ValueError) as error:
                                context.issue(f'{exp}@{iteration} {category}/{distance}/{metric}：{error}')
                values['roma'] = metadata.get(exp)
                rows.append((prefix, values, metadata.get(f'{exp}-STARSTAR')))
            except (TypeError, KeyError, ValueError, IndexError, AttributeError) as error:
                context.issue(f'{exp}@{iteration}：评测 schema 不匹配：{error}')
        _finish_rows(rows, 160)

    def make_bev_perf_table(results, selection):
        _detector_table(results, selection, new=False)

    def make_bev_new_perf_table(results, selection):
        _detector_table(results, selection, new=True)

    def get_iter_num(num):
        if num < 1000:
            return str(num)
        elif num % 1000 == 0:
            return f'{num // 1000}k'
        else:
            num_float = num / 1000
            if num % 100 == 0:
                return f'{num_float:.1f}k'
            elif num % 10 == 0:
                return f'{num_float:.2f}k'
            else:
                return f'{num_float:.3f}k'

    def show_bev_full_perf():
        metric_item = pills('评测项', ('Detect', 'Ghost', 'CutIn', 'Whitelist'))
        if metric_item == '白名单可视化':
            eval_and_vis_bev_model()
            return
        metric_select = {}
        if metric_item == 'Detect':
            col0, col1, col2, advanced = st.columns([2, 1.5, 2, 1.5])
            with col0:
                far_distance = st.selectbox('评测距离', ('前向近距离', '侧后向近距离', '前向远距离分区间', '后向远距离分区间', '动静'))
            with col1:
                target = st.selectbox('目标', ('全量目标', '关键目标'))
            with col2:
                metric = st.selectbox('评测指标', ('ap(p,r)(value)', 'ap', 'velocity_error(vel_degree)', 'mov_ap', 'moving_pr', 'stationary_pr'))
            with advanced:
                st.caption('高级指标参数')
                with st.popover('IoU / 类别', use_container_width=True):
                    iou_opt = st.selectbox('IoU', ('IoU@0.5 PedDist@1.0', 'IoU@0.5 PedDist@1.5', 'IoU@0.7 PedDist@0.5', 'IoU@0.4 PedDist@1.25', 'IoU@0.3 PedDist@1.5'))
                    cyclist_distance = st.selectbox('cyclist使用距离评测', ('否', '是'))
                    noshow_headcone = st.selectbox('不展示两截车和锥桶指标', ('是', '否'))
            st.caption(f'{iou_opt} · cyclist距离评测：{cyclist_distance} · 隐藏两截车和锥桶：{noshow_headcone}')
            if far_distance == '前向近距离':
                if cyclist_distance == '是':
                    trgt_name = 'bev_det_cyclist_distance.json' if target == '全量目标' else 'bev_det_cyclist_distance_kof.json'
                else:
                    trgt_name = 'bev_det.json' if target == '全量目标' else 'bev_det_kof.json'
            if far_distance == '侧后向近距离':
                if cyclist_distance == '是':
                    trgt_name = 'bev_det_rear_cyclist_distance.json' if target == '全量目标' else 'bev_det_rear_cyclist_distance_kof.json'
                else:
                    trgt_name = 'bev_det_rear.json' if target == '全量目标' else 'bev_det_rear_kof.json'
            if far_distance == '前向远距离分区间':
                if cyclist_distance == '是':
                    trgt_name = 'bev_det_front_far_cyclist_distance.json' if target == '全量目标' else 'bev_det_front_far_cyclist_distance_kof.json'
                else:
                    trgt_name = 'bev_det_front_far.json' if target == '全量目标' else 'bev_det_front_far_kof.json'
            if far_distance == '后向远距离分区间':
                if cyclist_distance == '是':
                    trgt_name = 'bev_det_rear_far_cyclist_distance.json' if target == '全量目标' else 'bev_det_rear_far_cyclist_distance_kof.json'
                else:
                    trgt_name = 'bev_det_rear_far.json' if target == '全量目标' else 'bev_det_rear_far_kof.json'
            if far_distance == '动静':
                trgt_name = 'bev_move.json'
            metric_select['metric_item'] = metric_item
            metric_select['metric'] = metric
            metric_select['iou_opt'] = iou_opt
            metric_select['noshow_headcone'] = noshow_headcone
            metric_select['far_distance'] = far_distance
        if metric_item == 'Ghost':
            trgt_name = 'bev_ghost_probe.json'
            metric_select['metric_item'] = metric_item
            metric_select['metric'] = ''
        if metric_item == 'CutIn':
            col0, col1, col2, col3, col4, col5 = st.columns(6)
            with col0:
                vis_frame = st.selectbox('显示时间戳', ('否', '是'))
            trgt_name = 'bev_cutin_latency.json'
            metric_select['metric_item'] = metric_item
            metric_select['metric'] = vis_frame
        if metric_item == 'Track':
            trgt_name = 'bev_track.json'
            metric_select['metric_item'] = metric_item
            metric_select['metric'] = ''
        if metric_item == 'Long-Short-Stop':
            col0, col1, col2, col3, col4, col5 = st.columns(6)
            with col0:
                score_thrs = str(st.slider('分数阈值', value=0.8, min_value=0.0, max_value=0.95, step=0.05))
            with col1:
                attribute = st.selectbox('速度', ('all', 'static', 'slow', 'moving'))
            trgt_name = 'bev_stop.json'
            metric_select['metric_item'] = metric_item
            metric_select['score_thrs'] = score_thrs
            metric_select['metric'] = ''
            metric_select['attribute'] = attribute
        if metric_item == 'Whitelist':
            col0, col1, col2 = st.columns(3)
            with col0:
                iou_opt = st.selectbox('IoU', ('IoU@0.7 PedDist@0.5', 'IoU@0.5 PedDist@1.0'))
            with col1:
                metric = st.selectbox('评测指标', ('all', 'ap', 'Recall', 'Precision'))
            with col2:
                whilelist_class = st.selectbox('障碍物类型', ('all', 'conetank', 'traffic_stick', 'animal', 'firehydrant', 'movable_obstacle', 'waterhorse', 'warning_triangle', 'lightpole', 'crash_barrel', 'parking_gate', 'low_obstacle', 'inverted_trafficcone', 'tyres'))
            metric_select['metric_item'] = metric_item
            metric_select['metric'] = metric
            metric_select['iou_opt'] = iou_opt
            metric_select['whilelist_class'] = whilelist_class
            trgt_name = 'result.json'
        if metric_item == 'tensorboard':
            st.subheader('根据关键词模糊匹配训练实验名, 并绘制tensorboard图')
            with st.form('submit_draw') as f:
                txt_x = st.text_area('训练任务名全称或关键词\n', placeholder='20250215-v41.14.1-sparsehead-stophead-sparsebev-stop-120k\n20250215-v41.14.2-sparsehead-stophead-sparsebev-stop-120k')
                submitted = st.form_submit_button()
            if submitted:
                show_tensorboard(txt_x)
                return
        if metric_item != 'tensorboard':
            all_bev_evals = read_eval_rslts(trgt_name, only_latest, is_pv=False)
            make_bev_perf_table(all_bev_evals, metric_select)

    def show_cog_full_perf():
        all_cog_evals = read_eval_rslts('cog_eval.json', only_latest, is_pv=False)
        metric_vals = ['lane', 'lane direction', 'lane type', 'boundary', 'road', 'curb', 'intersection']
        chosen = pills('指标', metric_vals, index=0)
        make_cog_perf_table_v2(all_cog_evals, chosen)

    def make_cog_perf_table_v2(exp_iter_2_val, chosen_metric):
        lines = []
        header = None
        exp_iter_2_val, exp2ext_info = exp_iter_2_val
        for (exp_name_, iter_num_), val in exp_iter_2_val.items():
            try:
                exp_time = 0
                main_name = exp_name_
                numeric_iter = iter_num_
                iter_num_ = get_iter_num(iter_num_)
                header_ = ['Exp']
                fields_ = [exp_time, main_name, numeric_iter, f'{iter_num_}@{main_name}' if iter_first else f'{main_name}@{iter_num_}']
                sub_metrics = val[chosen_metric]
                for key, value in sub_metrics.items():
                    header_.append(f'{key}@f1(p,r,iou)')
                    fields_.append('Na')
                    try:
                        fields_[-1] = f"{float(value['F1']):.3f}({float(value['precision']):.3f},                                    {float(value['Recall']):.3f},{float(value['IoU']):.3f})"
                    except Exception as e:
                        context.issue(f'指标缺失或格式不匹配：{e}')
                        fields_[-1] = f'{float(value[3]):.3f}({float(value[1]):.3f},{float(value[2]):.3f},                                     {float(value[0]):.3f})'
                if header is None:
                    header_.append('roma')
                    header = header_
                fields_.append(exp2ext_info.get(exp_name_))
                fields_.append(exp2ext_info.get(f'{exp_name_}-STARSTAR'))
                collect_line(lines, fields_, header_)
            except Exception as e:
                context.issue(f'指标缺失或格式不匹配：{e}')
                pass
        if header is None:
            st.write('Nothing to show')
            return
        data, data_pined, gb = prepare_lines_and_table(lines, header, 460)
        for x in header[1:]:
            gb.configure_column(field=x, header_name=x, width=200)
        gb = config_url(gb)
        gos = gb.build()
        gos['pinnedTopRowData'] = data_pined
        height = 34 + 28 * min(17, len(data) + len(data_pined) + 0.5)
        AgGrid(data, gridOptions=gos, height=height, allow_unsafe_jscode=True)

    def make_god_perf_table(all_god_evals, keys):
        lines = []
        header = None
        exp_iter_2_val, exp2ext_info = all_god_evals
        range_select, metric_select, noshow_bush = keys
        for (exp_name_, iter_num_), val in exp_iter_2_val.items():
            try:
                exp_time = 0
                main_name = exp_name_
                numeric_iter = iter_num_
                iter_num_ = get_iter_num(iter_num_)
                fields_ = [exp_time, main_name, numeric_iter, f'{iter_num_}@{main_name}' if iter_first else f'{main_name}@{iter_num_}']
                header_ = ['Exp']
                width = 107
                if metric_select in ('god_occupancy', 'god_semantic_all', 'god_semantic_thrs', 'god_visibility'):
                    if noshow_bush == '是':
                        clses = ['OTHERS', 'ROAD', 'DYNAMIC', 'CURB', 'UNOCCUPANCY']
                    else:
                        clses = ['OTHERS', 'ROAD', 'BUSH', 'DYNAMIC', 'CURB', 'UNOCCUPANCY', 'ROOF']
                    if metric_select == 'god_occupancy':
                        clses.insert(0, 'All')
                    if metric_select == 'god_visibility':
                        clses.insert(0, 'Visible_area(value=1)')
                    for ii, each_cls in enumerate(clses):
                        if metric_select == 'god_visibility' and ii == 0:
                            cls_name = 'Visi'
                        else:
                            cls_name = each_cls[:4]
                        header_.extend([f'{cls_name}-iou/f1', f'{cls_name}-P/R'])
                        fields_.extend(['Na', 'Na'])
                        try:
                            fields = [100 * float(x) for x in val[range_select][metric_select][each_cls]]
                            fields = [f'{x:.2f}' if x < 100 else '100' for x in fields]
                            fields_[-2] = f'{fields[0]}/{fields[3]}'
                            fields_[-1] = f'{fields[1]}/{fields[2]}'
                        except Exception as e:
                            context.issue(f'指标缺失或格式不匹配：{e}')
                            pass
                elif metric_select in ('god_flow_ve', 'god_flow_di', 'god_SD_pr'):
                    width = 120
                    if metric_select == 'god_flow_ve':
                        clses = ['Static(<=0.3m/s)', 'Slow(<=5m/s)', 'Fast(>5m/s)']
                    elif metric_select == 'god_flow_di':
                        clses = ['abs_direction<=10', 'abs_direction>10']
                    elif metric_select == 'god_SD_pr':
                        clses = ['static', 'dynamic']
                    for ii, each_cls in enumerate(clses):
                        cls_name = each_cls
                        if metric_select == 'god_flow_ve':
                            cls_name = ['static', 'slow', 'fast'][ii]
                        elif metric_select == 'god_flow_di':
                            cls_name = ['10>=dir', '10<dir'][ii]
                        if metric_select in ('god_flow_ve', 'god_flow_di'):
                            items = ['mean', 'std', '1-Sigma[65dot26%]']
                        elif metric_select in ('god_SD_pr',):
                            items = ['precision', 'recall', 'f1']
                        header_.extend([f'{cls_name}-{it}' for it in items])
                        fields_.extend(['Na', 'Na', 'Na'])
                        try:
                            cur_vals = val[range_select][metric_select][each_cls]
                            if isinstance(cur_vals, dict):
                                cur_vals = [cur_vals[name] for name in items]
                            fields_[-3] = cur_vals[0]
                            fields_[-2] = cur_vals[1]
                            fields_[-1] = cur_vals[2]
                        except Exception as e:
                            context.issue(f'指标缺失或格式不匹配：{e}')
                            pass
                elif metric_select in ('god_drivable',):
                    width = 117
                    clses = ['Drivable_space(value=1)', 'Drivable_boundary(width=1.6m)']
                    for ii, each_cls in enumerate(clses):
                        cls_name = 'Space' if ii == 0 else 'Bound'
                        header_.extend([f'{cls_name}-iou/f1', f'{cls_name}-P/R'])
                        fields_.extend(['Na', 'Na'])
                        if each_cls not in val[range_select][metric_select]:
                            continue
                        try:
                            fields = [100 * float(x) for x in val[range_select][metric_select][each_cls]]
                            fields = [f'{x:.2f}' if x < 100 else '100' for x in fields]
                            fields_[-2] = f'{fields[0]}/{fields[3]}'
                            fields_[-1] = f'{fields[1]}/{fields[2]}'
                        except Exception as e:
                            context.issue(f'指标缺失或格式不匹配：{e}')
                            pass
                    for range_ in ('RANGE_0(115.2m*44.8m)', 'RANGE_1(0-30m)', 'RANGE_2(30-60m)', 'RANGE_3(60-95.4m)', 'RANGE_0(115.2m*12m)', 'RANGE_1(0-30m*12m)', 'RANGE_2(30-60m*12m)', 'RANGE_3(60-95.4m*12m)'):
                        cls_name = range_.replace('.', 'dot').replace('RANGE_', 'RG_')
                        header_.extend([f'{cls_name}-Err/perc', f'{cls_name}-2sigSD'])
                        fields_.extend(['Na', 'Na'])
                        metric_name = 'drivable_boundary'
                        try:
                            h_err = val[metric_name][range_][0]
                            percent = val[metric_name][range_][2]
                            two_sigma_sd = val[metric_name][range_][1]
                            fields_[-2] = f'{100 * float(h_err):.2f}/{float(percent):.2f}'
                            fields_[-1] = f'({100 * float(two_sigma_sd[0]):.2f}/{100 * float(two_sigma_sd[1]):.2f})'
                        except Exception as e:
                            context.issue(f'指标缺失或格式不匹配：{e}')
                            pass
                        cls_name = range_.replace('.', 'dot').replace('RANGE_', 'RG_')
                        header_.extend([f'{cls_name}-Inner-Err/perc', f'{cls_name}-Inner-2sigSD'])
                        fields_.extend(['Na', 'Na'])
                        metric_name = 'drivable_boundary_inner'
                        try:
                            h_err = val[metric_name][range_][0]
                            percent = val[metric_name][range_][2]
                            two_sigma_sd = val[metric_name][range_][1]
                            fields_[-2] = f'{100 * float(h_err):.2f}/{float(percent):.2f}'
                            fields_[-1] = f'({100 * float(two_sigma_sd[0]):.2f}/{100 * float(two_sigma_sd[1]):.2f})'
                        except Exception as e:
                            context.issue(f'指标缺失或格式不匹配：{e}')
                            pass
                        cls_name = range_.replace('.', 'dot').replace('RANGE_', 'RG_')
                        header_.extend([f'{cls_name}-Outer-Err/perc', f'{cls_name}-Outer-2sigSD'])
                        fields_.extend(['Na', 'Na'])
                        metric_name = 'drivable_boundary_outer'
                        try:
                            h_err = val[metric_name][range_][0]
                            percent = val[metric_name][range_][2]
                            two_sigma_sd = val[metric_name][range_][1]
                            fields_[-2] = f'{100 * float(h_err):.2f}/{float(percent):.2f}'
                            fields_[-1] = f'({100 * float(two_sigma_sd[0]):.2f}/{100 * float(two_sigma_sd[1]):.2f})'
                        except Exception as e:
                            context.issue(f'指标缺失或格式不匹配：{e}')
                            pass
                elif metric_select in ('god_obstacle',):
                    width = 100
                    metric_name = 'distance'
                    range = 'God obstacle detection distance @center dist [1, 5]'
                    for mode in ('Stable', 'Max'):
                        mode_name = 'max_det_dist' if mode == 'Max' else 'stable_det_dist'
                        for each_cls in ('ALL', 'Cone', 'Fake_Pedestrian_Car', 'Fake_Pedestrian', 'Carton_S', 'Carton_L', 'Water_Barrier', 'Fence', 'Cone_S', 'Anti_Collision_Bucket', 'Triangle', 'Bicycle', 'Carton_M', 'Fake_Car', 'Traffic_Pole'):
                            header_.append(f'{each_cls}-{mode}')
                            fields_.append('Na')
                            try:
                                tmp_val = val[metric_name][range][each_cls][mode_name]
                                fields_[-1] = f'{float(tmp_val):.2f}'
                            except Exception as e:
                                context.issue(f'{exp_name_}@{numeric_iter}：缺少障碍物指标 {each_cls}/{mode_name}：{e}')
                elif metric_select in ('god_raeb_mis_det',):
                    width = 150
                    heads = ['false_detect_frames', 'false_detect_voxel_ratio']
                    header_.extend(heads)
                    try:
                        fields = [f"{val['false_detection_frames']}/{val['total_frames']}", val['false_detection_grids_ratio']]
                        fields_.extend(fields)
                    except Exception as e:
                        context.issue(f'指标缺失或格式不匹配：{e}')
                        fields_.extend(['Na' for _ in heads])
                elif metric_select in ('god_raeb_distance',):
                    width = 150
                    heads = ['recall@5m', 'recall@10m', 'stable distance', 'max distance']
                    header_.extend(heads)
                    try:
                        fields = [val['obstacle_pr']['God obstacle @iou 0.1']['BEV_dist@5']['recall'], val['obstacle_pr']['God obstacle @iou 0.1']['BEV_dist@10']['recall'], val['distance']['God obstacle detection distance @iou 0.1']['ALL']['stable_det_dist'], val['distance']['God obstacle detection distance @iou 0.1']['ALL']['max_det_dist']]
                        fields_.extend([f'{float(field):.2f}' for field in fields])
                    except Exception as e:
                        context.issue(f'指标缺失或格式不匹配：{e}')
                        fields_.extend(['Na' for _ in heads])
                elif metric_select in ('god_curb_inroad',):
                    width = 115
                    metric_name = 'acc5_'
                    for curb_range in ('0-20m', '20-40m', '40-60m', '60-90m')[::-1]:
                        for each_cls in ('Avg inroad distance', 'Inroad frames 0.4m+', 'Inroad frames 0.6m+', 'Inroad frames 1.0m+'):
                            cls_name = each_cls.replace('.', '_').replace('Inroad frames ', '')
                            header_.append(f'{cls_name}_{curb_range} frame')
                            fields_.append('Na')
                            try:
                                tmp_val = val[metric_name + curb_range].get(each_cls, 'Na')
                                fields_[-1] = tmp_val
                            except Exception as e:
                                context.issue(f'指标缺失或格式不匹配：{e}')
                                context.issue(f'指标解析提示：{e}')
                else:
                    pass
                if header is None:
                    header_.append('roma')
                    header = header_
                fields_.append(exp2ext_info.get(exp_name_))
                fields_.append(exp2ext_info.get(f'{exp_name_}-STARSTAR'))
                collect_line(lines, fields_, header_)
            except Exception as e:
                context.issue(f'指标缺失或格式不匹配：{e}')
                pass
        if header is None:
            st.write('Nothing to show')
            return
        data, data_pined, gb = prepare_lines_and_table(lines, header)
        for x in header[1:]:
            gb.configure_column(field=x, header_name=x, width=width)
        gb = config_url(gb)
        gos = gb.build()
        gos['pinnedTopRowData'] = data_pined
        AgGrid(data, gridOptions=gos, height=515, allow_unsafe_jscode=True)

    def show_god_full_perf():
        col1, col2, col3 = st.columns(3)
        with col1:
            key_1 = st.selectbox('评测项目', ('LITE_GOD(115.2m*44.8m)', 'VISIBLE_LITE_GOD(115.2m*44.8m)', 'LITE_GOD(115.2m*25.6m)', 'VISIBLE_LITE_GOD(115.2m*25.6m)', 'L3(140.8m*89.6m)', 'RAEB(5m*7m)', 'RAEB(11m*18m)'), key='god_select1')
        with col2:
            god_metrics = ('God occupancy', 'God semantic(All)', 'God semantic(occ_conf >= thrs)', 'God flow velocity error metric (DYNAMIC Object)', 'God flow direction error metric (DYNAMIC Object)', 'God stationary/moving PR_metric(DYNAMIC Object)', 'God visibility', 'God drivable', 'God Obstacle', 'God RAEB误触发', 'God RAEB漏触发', 'God Curb Inroad (Accumulative 5 frames)')
            key_2 = st.selectbox('评测指标', god_metrics, key='god_select2')
            key_2 = god_metrics.index(key_2)
            key_2 = ('god_occupancy', 'god_semantic_all', 'god_semantic_thrs', 'god_flow_ve', 'god_flow_di', 'god_SD_pr', 'god_visibility', 'god_drivable', 'god_obstacle', 'god_raeb_mis_det', 'god_raeb_distance', 'god_curb_inroad')[key_2]
        with col3:
            key_3 = st.selectbox('不展示BUSH和ROOF指标', ('是', '否'), key='god_select3')
        tgt_name_dict = {'god_obstacle': 'god_obstacle.json', 'god_curb_inroad': 'god_curb_inroad.json', 'god_raeb_mis_det': 'god_raeb_obstacle_mis_detect.json', 'god_raeb_distance': 'god_raeb_obstacle_distance.json'}
        trgt_name = 'god_eval.json' if key_2 not in tgt_name_dict else tgt_name_dict[key_2]
        all_god_evals = read_eval_rslts(trgt_name, only_latest, is_pv=False)
        make_god_perf_table(all_god_evals, (key_1, key_2, key_3))

    def show_pnc_full_perf():
        multi_name = 'pnc_eval.json'
        all_pnc_evals_v2, ext_info_v2 = read_eval_rslts(multi_name, only_latest, is_pv=False)
        metric_vals = ['fde', 'ade']
        chosen = pills('指标', metric_vals, index=0)
        make_pnc_perf_table_v2((all_pnc_evals_v2, ext_info_v2), chosen)

    def dict_2_formatvalue(x):
        ret = []
        for k, v in x.items():
            if int(v) == v:
                v = int(v)
            else:
                v = f'{v:.3f}'
            ret.append(f'{k}:{v}')
        return ret

    def _pnc_fill_normal_eval(header_, fields_, val, chosen_metric):
        if chosen_metric not in ('evaled/total_num', '基本面'):
            header_.append('Average')
            fields_.append('Na')

        def _get_fde_val(val, dt_):
            return f"{val[dt_]['FDE_30']:.3f}/{val[dt_]['FDE_50']:.3f}"

        def _get_ade_val(val, dt_):
            return f"{val[dt_]['ADE_30']:.3f}/{val[dt_]['ADE_50']:.3f}"
        metric_val_func = {'fde': _get_fde_val, 'ade': _get_ade_val}
        metric_val_key = {'fde': '30/50', 'ade': '30/50'}
        datasets_ = ['follow', 'merge_in', 'ugm', 'light', 'lane_change2', 'cgm']
        if chosen_metric == '基本面':
            for item, func in metric_val_func.items():
                eval_values = []
                for dt_ in datasets_:
                    try:
                        eval_value = func(val, dt_)
                    except Exception as _metric_error:
                        context.issue(f'指标缺失或格式不匹配：{_metric_error}')
                        eval_value = 'Na'
                    eval_values.append(eval_value)
                header_.append(f'{item}@{metric_val_key[item]}')
                fields_.append('Na')
                vals = np.array([[float(_) for _ in x.split('/')] for x in eval_values if x != 'Na'])
                if vals.shape[0] > 0:
                    vals = vals.mean(axis=0).flatten()
                    fields_[-1] = '/'.join([f'{float(x):.3f}' for x in vals])
        for dt_ in datasets_:
            if chosen_metric == 'fde':
                header_.append(f'fde30/50@{dt_}')
                fields_.append('Na')
                try:
                    fields_[-1] = metric_val_func[chosen_metric](val, dt_)
                except Exception as _metric_error:
                    context.issue(f'指标缺失或格式不匹配：{_metric_error}')
                    pass
            elif chosen_metric == 'ade':
                header_.append(f'ade30/50@{dt_}')
                fields_.append('Na')
                try:
                    fields_[-1] = metric_val_func[chosen_metric](val, dt_)
                except Exception as _metric_error:
                    context.issue(f'指标缺失或格式不匹配：{_metric_error}')
                    pass
        try:
            if chosen_metric not in ('evaled/total_num', '基本面'):
                vals = np.array([[float(_) for _ in x.split('/')] for x in fields_[-6:] if x != 'Na'])
                if len(vals) > 0:
                    vals = vals.mean(axis=0).flatten()
                    fields_[-7] = '/'.join([f'{float(x):.3f}' for x in vals])
        except Exception as _metric_error:
            context.issue(f'指标缺失或格式不匹配：{_metric_error}')
            pass

    def make_pnc_perf_table_v2(exp_iter_2_val, chosen_metric):
        lines = []
        header = None
        exp_iter_2_val, exp2ext_info = exp_iter_2_val
        for (exp_name_, iter_num_), val in exp_iter_2_val.items():
            try:
                exp_time = 0
                main_name = exp_name_
                numeric_iter = iter_num_
                iter_num_ = get_iter_num(iter_num_)
                header_ = ['Exp']
                fields_ = [exp_time, main_name, numeric_iter, f'{iter_num_}@{main_name}' if iter_first else f'{main_name}@{iter_num_}']
                _pnc_fill_normal_eval(header_, fields_, val, chosen_metric)
                if header is None:
                    header_.append('roma')
                    header = header_
                fields_.append(exp2ext_info.get(exp_name_))
                fields_.append(exp2ext_info.get(f'{exp_name_}-STARSTAR'))
                collect_line(lines, fields_, header_)
            except Exception as _metric_error:
                context.issue(f'指标缺失或格式不匹配：{_metric_error}')
                pass
        if header is None:
            st.write('Nothing to show')
            return
        data, data_pined, gb = prepare_lines_and_table(lines, header, 460)
        for x in header[1:]:
            ww = 100 if chosen_metric == '难例' else 155
            gb.configure_column(field=x, header_name=x, width=ww)
        gb = config_url(gb)
        gb.configure_selection('multiple', use_checkbox=True)
        go = gb.build()
        go['pinnedTopRowData'] = data_pined
        height = 34 + 28 * min(17, len(data) + len(data_pined) + 0.5)
        grid_return = AgGrid(data, gridOptions=go, height=height, allow_unsafe_jscode=True, data_return_mode=DataReturnMode.AS_INPUT, update_mode=GridUpdateMode.MODEL_CHANGED)
        selected_rows = grid_return['selected_rows']

    def make_navi_perf_table(exp_iter_2_val, chosen_metric):
        lines = []
        header = None
        exp_iter_2_val, exp2ext_info = exp_iter_2_val
        for (exp_name_, iter_num_), val in exp_iter_2_val.items():
            try:
                exp_time = 0
                main_name = exp_name_
                numeric_iter = iter_num_
                iter_num_ = get_iter_num(iter_num_)
                header_ = ['Exp']
                fields_ = [exp_time, main_name, numeric_iter, f'{iter_num_}@{main_name}' if iter_first else f'{main_name}@{iter_num_}']
                sub_metrics = val[chosen_metric]
                for key, value in sub_metrics.items():
                    header_.append(f'{key}-avg(count)')
                    fields_.append('Na')
                    try:
                        fields_[-1] = f"{float(value['avg']):.3f}({value['count']})"
                    except Exception as e:
                        context.issue(f'指标缺失或格式不匹配：{e}')
                        fields_[-1] = f'{float(value[3]):.3f}({float(value[1]):.3f})'
                if header is None:
                    header_.append('roma')
                    header = header_
                fields_.append(exp2ext_info.get(exp_name_))
                fields_.append(exp2ext_info.get(f'{exp_name_}-STARSTAR'))
                collect_line(lines, fields_, header_)
            except Exception as e:
                context.issue(f'指标缺失或格式不匹配：{e}')
                pass
        if header is None:
            st.write('Nothing to show')
            return
        data, data_pined, gb = prepare_lines_and_table(lines, header, 460)
        for x in header[1:]:
            gb.configure_column(field=x, header_name=x, width=200)
        gb = config_url(gb)
        gos = gb.build()
        gos['pinnedTopRowData'] = data_pined
        height = 34 + 28 * min(17, len(data) + len(data_pined) + 0.5)
        AgGrid(data, gridOptions=gos, height=height, allow_unsafe_jscode=True)

    def show_bev_new_full_perf():
        metric_item = pills('评测项', ('Detect', 'Ghost', 'tensorboard', 'CutIn_Latency'))
        metric_select = {}
        if metric_item == 'Detect':
            col0, col1, col2, advanced = st.columns([2, 1.5, 2, 1.5])
            with col0:
                far_distance = st.selectbox('评测距离', ('前向近距离', '侧后向近距离', '其他区域', '侧向远距离'))
            with col1:
                target = st.selectbox('目标', ('关键目标', '全量目标', '关键区域目标'))
            with col2:
                metric = st.selectbox('评测指标', ('(p,r)(value)', 'ap', 'velocity_error', 'velocity_degree', 'moving_pr', 'stationary_pr', 'heading_degree', 'corner_diffs'))
            with advanced:
                st.caption('高级指标参数')
                with st.popover('IoU / 类别', use_container_width=True):
                    iou_opt = st.selectbox('IoU', ('IoU@0.5 PedDist@1.0', 'IoU@0.5 PedDist@1.5', 'IoU@0.7 PedDist@0.5'))
                    cyclist_distance = st.selectbox('cyclist使用距离评测', ('否', '是'))
                    noshow_headcone = st.selectbox('不展示锥桶指标', ('是', '否'))
            st.caption(f'{iou_opt} · cyclist距离评测：{cyclist_distance} · 隐藏锥桶：{noshow_headcone}')
            if far_distance == '前向近距离':
                if cyclist_distance == '是':
                    trgt_name = 'bev_det_cyclist_distance.json' if target == '全量目标' else 'bev_det_cyclist_distance_kof.json'
                else:
                    trgt_name = 'bev_det.json' if target == '全量目标' else 'bev_det_kof.json'
                    if target == '关键区域目标':
                        trgt_name = 'bev_det_key_region.json'
            if far_distance == '侧后向近距离':
                if cyclist_distance == '是':
                    trgt_name = 'bev_det_rear_cyclist_distance.json' if target == '全量目标' else 'bev_det_rear_cyclist_distance_kof.json'
                else:
                    trgt_name = 'bev_det_rear.json' if target == '全量目标' else 'bev_det_rear_kof.json'
            if far_distance == '其他区域':
                if cyclist_distance == '是':
                    trgt_name = 'bev_det_other_cyclist_distance.json' if target == '全量目标' else 'bev_det_other_cyclist_distance_kof.json'
                else:
                    trgt_name = 'bev_det_other.json' if target == '全量目标' else 'bev_det_other_kof.json'
            if far_distance == '侧向远距离':
                trgt_name = 'bev_det_lateral.json' if target == '全量目标' else 'bev_det_lateral_kof.json'
            metric_select['metric_item'] = metric_item
            metric_select['metric'] = metric
            metric_select['iou_opt'] = iou_opt
            metric_select['noshow_headcone'] = noshow_headcone
            metric_select['far_distance'] = far_distance
        if metric_item == 'Ghost':
            trgt_name = 'bev_ghost_probe.json'
            metric_select['metric_item'] = metric_item
            metric_select['metric'] = ''
        if metric_item == 'tensorboard':
            st.subheader('根据关键词模糊匹配训练实验名, 并绘制tensorboard图')
            with st.form('submit_draw') as f:
                txt_x = st.text_area('训练任务名全称或关键词\n', placeholder='20250215-v41.14.1-sparsehead-stophead-sparsebev-stop-120k\n20250215-v41.14.2-sparsehead-stophead-sparsebev-stop-120k')
                submitted = st.form_submit_button()
            if submitted:
                show_tensorboard(txt_x)
                return
        if metric_item == 'CutIn_Latency':
            trgt_name = 'bev_cutin_latency_new.json'
            metric_select['metric_item'] = metric_item
            metric_select['metric'] = ''
        if metric_item != 'tensorboard':
            all_bev_evals = read_eval_rslts(trgt_name, only_latest, is_pv=False)
            make_bev_new_perf_table(all_bev_evals, metric_select)

    def make_pv_perf_table(all_pv_evals):
        records, metadata = all_pv_evals
        expected = ['ground', 'vehicle', 'pedestrian and rider', 'isolation zone on the road',
                    'sidewalk', 'other obstacles', 'non-motor vehicle and motorcycle', 'sky',
                    'vegetation', 'lane line', 'zebra crossing', 'road marking', 'stopline',
                    'ego vehicle', 'whole curb', 'road boundary', 'divergence', 'deceleration', 'parking slot']
        rows = []
        for (exp, iteration), payload in records.items():
            prefix, label = _prefix(exp, iteration)
            values = {'Exp': label}
            try:
                for key, column in [('mIoU', 'IoU'), ('mPrecision', 'mPrecision'), ('mRecall', 'mRecall')]:
                    values[column] = payload.get(key, '—')
                supplied = payload.get('cls')
                if not isinstance(supplied, list) or not all(isinstance(value, str) for value in supplied):
                    supplied = expected
                    context.issue(f'{exp}@{iteration}：PV cls 非字符串列表，按原看板固定类别顺序展示')
                for category in expected:
                    column = f'{category}(IoU/Pr/Rc)'
                    values[column] = '—'
                    if category not in supplied:
                        context.issue(f'{exp}@{iteration}：PV 缺少类别 {category}')
                        continue
                    index = supplied.index(category)
                    try:
                        values[column] = '/'.join(str(round(float(payload[key][index]), 4))
                                                  for key in ('iou', 'precision', 'recall'))
                    except (KeyError, IndexError, ValueError, TypeError) as error:
                        context.issue(f'{exp}@{iteration} PV {category}：{error}')
                for camera in (1, 2, 4, 5, 6, 7, 8):
                    column = f'cam{camera}(mIoU/mPr/mRc)'
                    values[column] = '—'
                    try:
                        camera_values = payload[f'cam_{camera}']
                        values[column] = '/'.join(str(camera_values[key])
                                                  for key in ('mIoU', 'mPrecision', 'mRecall'))
                    except (KeyError, TypeError) as error:
                        context.issue(f'{exp}@{iteration} PV cam_{camera}：{error}')
                rows.append((prefix, values, metadata.get(f'{exp}-STARSTAR')))
            except (TypeError, ValueError, AttributeError) as error:
                context.issue(f'{exp}@{iteration} PV schema 不匹配：{error}')
        _finish_rows(rows, 150)

    def make_depth_perf_table(exp_iter_2_val, depth_type):
        lines = []
        header = None
        exp_iter_2_val, exp2ext_info = exp_iter_2_val
        depth_metrics = {'mae(m)': 'Mae(m)', 'abs_rel(%)': 'Abs_avg_rel(%)', 'abs_rel_25(m)': 'MaxDep 0-25%(m)', 'abs_rel_50(m)': 'MaxDep 0-50%(m)', 'abs_rel_75(m)': 'MaxDep 0-75%(m)', 'a1(%)': 'AbsRel 0-25%(%)', 'a2(%)': 'AbsRel 0-50%(%)', 'a3(%)': 'AbsRel 0-75%(%)', 'avg_samples(px)': 'Avg_samples(px)'}
        for (exp_name_, iter_num_), val in exp_iter_2_val.items():
            try:
                exp_time = 0
                main_name = exp_name_
                numeric_iter = iter_num_
                iter_num_ = get_iter_num(iter_num_)
                fields_ = [exp_time, main_name, numeric_iter, f'{iter_num_}@{main_name}' if iter_first else f'{main_name}@{iter_num_}']
                header_ = ['Exp']
                for metric, column in depth_metrics.items():
                    header_.append(f'{column}')
                    try:
                        metric_value = val[depth_type][metric]
                        if isinstance(metric_value, (int, float)):
                            fields_.append(f'{metric_value:.2f}')
                        else:
                            fields_.append(str(metric_value))
                    except Exception as e:
                        context.issue(f'指标缺失或格式不匹配：{e}')
                        fields_.append('Na')
                if header is None:
                    header_.append('roma')
                    header = header_
                fields_.append(exp2ext_info.get(exp_name_))
                fields_.append(exp2ext_info.get(f'{exp_name_}-STARSTAR'))
                collect_line(lines, fields_, header_)
            except Exception as e:
                context.issue(f'指标缺失或格式不匹配：{e}')
                pass
                continue
        if header is None:
            st.write('暂无Depth数据可展示')
            return
        data, data_pined, gb = prepare_lines_and_table(lines, header, 200)
        for x in header[1:]:
            if 'samples' in x:
                gb.configure_column(field=x, header_name=x, width=120)
            else:
                gb.configure_column(field=x, header_name=x, width=140)
        gb = config_url(gb)
        gos = gb.build()
        gos['pinnedTopRowData'] = data_pined
        height = 34 + 28 * min(27, len(data) + len(data_pined) + 0.5)
        AgGrid(data, gridOptions=gos, height=height, allow_unsafe_jscode=True)

    def show_pv_full_perf():
        trgt_name = 'pvseg_rslt.json'
        all_pv_evals = read_eval_rslts(trgt_name, only_latest, is_pv=False)
        make_pv_perf_table(all_pv_evals)

    def show_depth_full_perf():
        depth_type = pills(label='评测项', options=['overall', 'front', 'surround', 'fisheye'], index=0, key='depth_pills')
        all_depth_evals = read_eval_rslts('depth_eval.json', only_latest, is_depth=True)
        st.subheader(f'{depth_type} 深度评测结果')
        make_depth_perf_table(all_depth_evals, depth_type)

    def show_navi_full_perf():
        metric_vals = ['ade_in_m', 'map_loss']
        navi_type = pills(label='评测项', options=metric_vals, index=0)
        all_navi_evals = read_eval_rslts('navi_eval.json', only_latest, is_depth=True)
        make_navi_perf_table(all_navi_evals, navi_type)

    tasks = {
        'god': show_god_full_perf, 'cog': show_cog_full_perf,
        'bev': show_bev_full_perf, 'pnc': show_pnc_full_perf,
        'pvseg': show_pv_full_perf, 'depth': show_depth_full_perf,
        'navi': show_navi_full_perf, 'bev-new': show_bev_new_full_perf,
    }
    if task not in tasks:
        raise ValueError(f'未知任务：{task}')
    try:
        tasks[task]()
    except ResultsPending:
        raise
    except Exception as error:
        context.issue(f'{task} 指标表解析失败：{error}')
        st.warning('这份评测数据与专用表格式不一致。请在基线对比中检查原始字段，并查看诊断详情。')
